using System;
using System.Collections.Generic;
using System.Globalization;
using ACadSharp.Blocks;
using ACadSharp.Entities;
using ACadSharp.Tables;
using CSMath;
using Viprs.Abi;
using Viprs.Wire;

// The walk that turns a resident ACadSharp document into the flattened
// primitive stream docs/WIRE.md describes.
//
// Two properties this file exists to hold:
//
//  1. No curve is tessellated. ARC, CIRCLE, ELLIPSE and SPLINE cross as
//     themselves with their own parameters, and a polyline bulge crosses as a
//     bulge. libviprs owns zoom and precision aware tessellation and is the
//     only layer that knows either, so anything this file decided would be
//     wrong at every zoom but one.
//  2. Block expansion never recurses on the CLR stack. The depth bound is a
//     number the caller sets through max_block_depth, a caller may set it to
//     four billion, and a bounded refusal that arrives as a stack overflow is
//     not a bounded refusal. The walk is an explicit stack of enumerators.
//
// The other bounds are not enforced here on purpose: RecordEncoder applies
// max_polyline_points and max_string_bytes as it encodes, DecodeSession
// counts against max_entities, and BatchWriter counts max_output_bytes.
// Applying a bound twice means two messages for one refusal and two places to
// get it wrong. max_block_depth is the one nothing above this file can see,
// because above this file a nested insertion is just more records.
namespace Viprs.Cad
{
	// `partial` because the SOLID and MESH arm bodies live in
	// Flatten.Faces.cs. The `case` labels stay in the switch below: their
	// order is semantics, the compiler enforces it, and splitting the labels
	// across files would put a rule nothing checks between two of them.
	internal sealed partial class Flattener
	{
		private const double Eps = 1e-12;

		// Bit 0 of a record's flags, from docs/WIRE.md's geometry prologue:
		// this record came out of expanding a nested insertion.
		private const uint FlagFromBlock = 1u;

		// How many items the walk handles between two reads of the cancel
		// flag.
		private const ulong CancelPollInterval = 1024ul;

		private readonly ResolvedLimits _limits;
		private ulong _entities;

		public Flattener(ResolvedLimits limits)
		{
			_limits = limits ?? ResolvedLimits.Defaults;
		}

		public ulong EntitiesVisited
		{
			get { return _entities; }
		}

		// One item the walk has still to deal with: either an entity to
		// flatten, or a record that is already made.
		//
		// The record slot is what lets a composite entity (a dimension, a
		// hatch) hand back a mixture of finished records and further entities
		// in one sequence, in the order a consumer should see them, without
		// any of it going back through Map recursively. That recursion was a
		// stack overflow reachable from a file: a DIMENSION whose block holds
		// a DIMENSION nested as deep as the file likes, and two dimensions
		// whose blocks hold each other nested forever. A StackOverflowException
		// cannot be caught, so the export's catch-all was never in the
		// picture; the runtime calls FailFast and the consumer's process dies.
		private struct Pending
		{
			public Entity Entity;
			public Primitive Record;
			public Placement Placement;
			public int Depth;
			// Non-zero when the record should be labelled with the handle of
			// the entity it was generated for rather than the synthesised one
			// it came from. CadObject.Handle has an internal setter, so a
			// hatch edge cannot be relabelled at the source.
			public ulong HandleOverride;
		}

		private struct Frame
		{
			public IEnumerator<Pending> Items;
		}

		// ------------------------------------------------------- geometry

		private static void Append(List<double> into, Placement place, XYZ p)
		{
			XYZ v = place.Apply(p);
			into.Add(v.X);
			into.Add(v.Y);
			into.Add(v.Z);
		}

		// ----------------------------------------------- object coordinates

		// A DWG entity does not store world coordinates. It stores them in the
		// object coordinate system its extrusion direction defines, and DXF's
		// arbitrary axis algorithm is what turns one into the other: the plane is
		// the one the normal names, and the algorithm's whole job is to pick a
		// repeatable x axis inside it.
		//
		// None of it ran until this landed, and on a corpus of +Z extrusions that
		// was invisible, because for +Z the algorithm is exactly the identity.
		// docs/WIRE.md's records 4, 5, 6, 7 and 9 each carry a normal so a
		// consumer knows which plane the record's angles and bulges are measured
		// in; they were carrying a plane the coordinates beside them were not in.

		// The band inside which the algorithm crosses the normal with the world y
		// axis instead of the world z axis.
		//
		// Near the z axis the cross product with z shrinks towards nothing and its
		// direction is decided by the last few bits of the normal, so without the
		// band two drawings that agree to twelve decimals get frames ninety
		// degrees apart. Upstream's Matrix4.GetArbitraryAxis writes the same
		// constant as `(1 / 64)`, which C# evaluates as integer division to zero,
		// so its test never fires and a normal of (1e-3, 0, 1) comes back with an
		// x axis the algorithm does not give it. That is why this file runs its
		// own, and why InsertMatrix below builds the INSERT transform here rather
		// than calling Insert.GetTransform().
		private const double ArbitraryAxisBand = 1.0 / 64.0;

		// A unit vector, and the one place this file divides by a length.
		//
		// A normal a drawing holds need not be a unit vector, and a damaged file
		// can hold three zeroes, which is not a plane and does not become one by
		// being divided by. That case keeps the world's own plane. A value that is
		// not finite goes straight through on purpose: Finite is what refuses that
		// one, and it refuses it naming the handle.
		private static XYZ Unit(XYZ v)
		{
			double len = v.GetLength();
			return len < Eps ? XYZ.AxisZ : new XYZ(v.X / len, v.Y / len, v.Z / len);
		}

		internal static XYZ ArbitraryAxisX(XYZ normal)
		{
			XYZ n = Unit(normal);
			XYZ x = Math.Abs(n.X) < ArbitraryAxisBand && Math.Abs(n.Y) < ArbitraryAxisBand
				? XYZ.Cross(XYZ.AxisY, n)
				: XYZ.Cross(XYZ.AxisZ, n);
			return Unit(x);
		}

		// The matrix that takes a point in that system to world space.
		//
		// For a normal of +Z it is the identity, exactly, which is why every
		// expectation recorded before this existed is unchanged by it.
		private static Matrix4 ObjectToWorld(XYZ normal)
		{
			XYZ n = Unit(normal);
			XYZ x = ArbitraryAxisX(n);
			XYZ y = XYZ.Cross(n, x);
			return new Matrix4(
				x.X, y.X, n.X, 0.0,
				x.Y, y.Y, n.Y, 0.0,
				x.Z, y.Z, n.Z, 0.0,
				0.0, 0.0, 0.0, 1.0
			);
		}

		// What a transform does to the plane an entity is drawn in, measured
		// rather than decomposed. A composed chain of insertions is not
		// guaranteed to decompose into the translate, rotate and scale it was
		// built from, and a measurement of the basis always is.
		//
		// The plane is the entity's own and not the world's XY: every field here
		// is read off a placement that already has the entity's object coordinate
		// system composed into it, so the two vectors behind it are the images of
		// the entity's own in-plane axes.
		internal struct Basis
		{
			// What the transform does to the length of each in-plane axis.
			public double ScaleX;
			public double ScaleY;

			// The plane the record says it is in, as a unit vector, on the same
			// side of that plane the entity's own normal was on.
			public XYZ Normal;

			// The angle from that normal's arbitrary axis to the image of the
			// entity's own x axis, counter-clockwise about the normal. An Arc's
			// angles are offset by it, and for a transform that stays inside the
			// world's XY plane it is the plain rotation the old code measured.
			public double Turn;

			// Whether the transform reverses handedness. An Arc's angles, an
			// Ellipse's parameters and a bulge are all counter-clockwise about
			// Normal, and a reflection reverses what that means, so all three have
			// a correction and all three are exact.
			public bool Mirrored;

			// Equal in-plane scale factors that are still at right angles to each
			// other, mirror or no mirror. This is the gate
			// NON_UNIFORM_BLOCK_SCALE hangs off.
			//
			// It used to be the two lengths and nothing else, which calls a 3x
			// scale composed with a forty-five degree rotation uniform: the two
			// images come out the same length, sqrt(5) each, and stop being
			// perpendicular. A circle under that is an ellipse, and this warning
			// is exactly the thing that should have said so.
			public bool Uniform;
		}

		// The three images of a frame's axes, as a Basis.
		//
		// `c` is only ever read for its sign. Handedness is the sign of the
		// determinant, which is the triple product of the three; the test used to
		// be the z component of a 2D cross product, which is the determinant of
		// the top-left 2x2 and says nothing about a transform that leaves the XY
		// plane. An insertion that stands the block on its side measured 0 there,
		// and a mirror in z measured positive.
		private static Basis MeasureBasis(XYZ a, XYZ b, XYZ c)
		{
			Basis basis = new Basis();
			basis.ScaleX = a.GetLength();
			basis.ScaleY = b.GetLength();

			XYZ n = XYZ.Cross(a, b);
			basis.Mirrored = n.Dot(c) < 0.0;

			// A cross product of length nothing is a transform that has collapsed
			// the plane onto a line, which a scale factor of zero does. There is no
			// plane left to name, so the third axis' image is the closest thing to
			// one and the record still says which way the entity faced.
			XYZ plane = n.GetLength() < Eps ? Unit(c) : Unit(n);
			basis.Normal = basis.Mirrored ? -plane : plane;

			XYZ u = ArbitraryAxisX(basis.Normal);
			XYZ v = XYZ.Cross(basis.Normal, u);
			basis.Turn = Math.Atan2(a.Dot(v), a.Dot(u));

			double m = Math.Max(Math.Max(basis.ScaleX, basis.ScaleY), 1.0);
			basis.Uniform = Math.Abs(basis.ScaleX - basis.ScaleY) <= 1e-9 * m
				&& Math.Abs(a.Dot(b)) <= 1e-9 * m * m;
			return basis;
		}

		// One transform, and what it measures out to, computed at most once.
		//
		// A transform belongs to a block instance, not to an entity: every entity
		// under one INSERT crosses under the same matrix. The basis used to be
		// recomputed for each of them, and before the switch rather than inside
		// it, so six 4x4 multiplies, three square roots and an atan2 ran for every
		// LINE, SPLINE, DIMENSION, HATCH and unsupported entity in the document,
		// none of which reads any of it. This holds the answer beside the
		// transform and works it out the first time something asks.
		//
		// WithOcs returning `this` for an extrusion of +Z is what keeps that
		// sharing: an entity in the world's own plane measures the placement it
		// was handed rather than a copy of it. An entity in some other plane gets
		// a placement composed for that plane, and the one beside it in the same
		// plane gets the same object back, so a run of entities that share an
		// extrusion measures once between them the way a run of +Z ones does.
		//
		// A class rather than a struct on purpose: the Pending items that share a
		// placement have to share the memo as well, and a struct would give each
		// of them a copy that measures again.
		internal sealed class Placement
		{
			public readonly Matrix4 Matrix;

			private Basis _basis;
			private bool _measured;

			// The last plane WithOcs was asked for, and what it gave back.
			//
			// One entry, not a dictionary. Entities that share an extrusion
			// arrive together far more often than not (a block drawn in one
			// plane, a drawing mirrored in one pass), and a run of them shares
			// the composition and the basis measured from it the way a run of
			// +Z entities shares this placement itself. A run that alternates
			// planes pays one comparison per entity for nothing, which is
			// cheaper than the 4x4 multiply it is deciding about.
			private XYZ _ocsNormal;
			private double _ocsElevation;
			private Placement _ocs;

			public Placement(Matrix4 matrix)
			{
				Matrix = matrix;
			}

			// Exactly what Transform.ApplyTransform does, which is the matrix
			// multiply and the RoundZero pass and nothing else.
			//
			// The matrix rather than the Transform, because CSMath's
			// Transform(Matrix4) constructor decomposes the matrix it is handed
			// into a translation, a scale and a quaternion turned into Euler
			// angles, allocating on the way, and this file has never read any of
			// the three. One composed insertion was paying for a full
			// decomposition it then threw away.
			public XYZ Apply(XYZ p)
			{
				return (Matrix * p).RoundZero();
			}

			// The same placement with an entity's own object coordinate system
			// composed in front of it, so a coordinate the entity stores can go to
			// Apply exactly as the file holds it.
			public Placement WithOcs(XYZ normal, double elevation)
			{
				bool flat = Math.Abs(normal.X) < Eps
					&& Math.Abs(normal.Y) < Eps
					&& normal.Z > 0.0;
				if (flat && elevation == 0.0)
				{
					return this;
				}

				if (_ocs != null && _ocsElevation == elevation && _ocsNormal == normal)
				{
					return _ocs;
				}

				Matrix4 ocs = ObjectToWorld(normal);
				if (elevation != 0.0)
				{
					ocs = Compose(
						ocs,
						Transform.CreateTranslation(new XYZ(0.0, 0.0, elevation)).Matrix
					);
				}

				_ocsNormal = normal;
				_ocsElevation = elevation;
				_ocs = new Placement(Compose(Matrix, ocs));
				return _ocs;
			}

			public Basis Basis
			{
				get
				{
					if (!_measured)
					{
						_measured = true;
						_basis = BasisOf(XYZ.AxisX, XYZ.AxisY);
					}

					return _basis;
				}
			}

			// The same measurement for an entity whose in-plane x axis is not the
			// arbitrary axis of its normal. An ELLIPSE is the one that needs it:
			// its major axis is on the record, in world coordinates, so its two
			// parameters are measured from that vector and not from a frame the
			// normal implies. Not memoised, because it is asked once per ellipse.
			public Basis BasisOf(XYZ u, XYZ v)
			{
				XYZ o = Apply(XYZ.Zero);
				return MeasureBasis(
					Apply(u) - o,
					Apply(v) - o,
					Apply(XYZ.Cross(u, v)) - o
				);
			}
		}

		private static Matrix4 Identity()
		{
			return Matrix4.Identity;
		}

		private static Matrix4 Compose(Matrix4 outer, Matrix4 inner)
		{
			return outer * inner;
		}

		// ----------------------------------------------------------- bounds

		// max_entities, applied to what the walk visits rather than only to
		// what it emits.
		//
		// The two are not the same number and the difference is a hole an
		// untrusted file walks straight through: an INSERT yields no record,
		// it pushes a frame, so a chain of block records each holding two
		// INSERTs of the next expands 2^depth times, emits nothing, and trips
		// neither the record count nor max_output_bytes. Twenty-one block
		// records and forty-one entities, a file of a few kilobytes, ran for
		// thirty-four seconds at depth twenty and is 2^65 expansions at the
		// default max_block_depth. The header's own wording is "counted across
		// the whole decode, block expansion included", which is this count.
		private void CountEntity()
		{
			_entities = _entities + 1ul;
			if (_entities > _limits.MaxEntities)
			{
				throw new AbiException(
					Result.LimitExceeded,
					"the decode reached entity "
						+ _entities.ToString(CultureInfo.InvariantCulture)
						+ " and max_entities is "
						+ _limits.MaxEntities.ToString(CultureInfo.InvariantCulture)
						+ ", counted across the whole decode with block expansion included"
				);
			}
		}

		// max_block_depth, applied to every kind of expansion and not only to
		// INSERT. A dimension's picture and a hatch's boundary nest the same
		// way and a file can nest either as deep as it likes, so a bound that
		// only counted insertions was a bound with a way round it.
		private void CheckDepth(int depth, string kind, ulong handle, string name)
		{
			if ((uint)depth <= _limits.MaxBlockDepth)
			{
				return;
			}

			throw new AbiException(
				Result.LimitExceeded,
				kind + " " + handle.ToString("X", CultureInfo.InvariantCulture)
					+ " (" + name + ") is at expansion depth "
					+ depth.ToString(CultureInfo.InvariantCulture)
					+ " and max_block_depth is "
					+ _limits.MaxBlockDepth.ToString(CultureInfo.InvariantCulture)
			);
		}

		// max_polyline_points, applied where the points are counted rather
		// than where they are encoded.
		//
		// The encoder does check, but it checks after the list exists: a
		// spline with a million control points is a 24 MB allocation inside
		// the library before anything compares it to the caller's bound, and
		// the encoder was not checking splines at all. Counting first means
		// the memory is never asked for.
		private void CheckPointCount(int points, string what)
		{
			if ((ulong)points <= _limits.MaxPolylinePoints)
			{
				return;
			}

			throw new AbiException(
				Result.LimitExceeded,
				what + " carries " + points.ToString(CultureInfo.InvariantCulture)
					+ " points and max_polyline_points is "
					+ _limits.MaxPolylinePoints.ToString(CultureInfo.InvariantCulture)
			);
		}

		// -------------------------------------------------------- the walk

		// `canceled` is polled inside the loop, not between batches.
		//
		// Between batches is where the export reads the caller's flag, and
		// that is only enough while every step of the walk produces a record.
		// A document can make this loop run for a long time producing none: a
		// chain of block records each holding several insertions of the next
		// expands exponentially, and twenty-one block records with forty-one
		// entities in them ran for thirty-four seconds without yielding once.
		// While that runs the export never gets control back, so a caller that
		// sets the flag is waiting on a decode that will never look at it.
		// Polling here is what makes cancellation mean anything.
		public IEnumerable<Primitive> Walk(IEnumerable<Entity> roots, Func<bool> canceled = null)
		{
			Stack<Frame> stack = new Stack<Frame>();
			stack.Push(new Frame { Items = Root(roots).GetEnumerator() });
			ulong sinceLastPoll = 0ul;

			try
			{
				while (stack.Count > 0)
				{
					// Not on every entity: reading a volatile through a
					// delegate on a hot loop is not free, and a thousand
					// entities is well under a millisecond even on the
					// pathological documents above.
					sinceLastPoll = sinceLastPoll + 1ul;
					if (sinceLastPoll >= CancelPollInterval)
					{
						sinceLastPoll = 0ul;
						if (canceled != null && canceled())
						{
							throw new AbiException(
								Result.Canceled,
								"the caller set cancel_flag while the decode was walking the document"
							);
						}
					}

					Frame frame = stack.Peek();
					if (!frame.Items.MoveNext())
					{
						stack.Pop();
						frame.Items.Dispose();
						continue;
					}

					Pending item = frame.Items.Current;

					// A record a composite already made. It is not an entity,
					// so it does not count against max_entities here; the
					// batch writer counts every record it emits separately.
					if (item.Record != null)
					{
						yield return Finite(item.Record);
						continue;
					}

					CountEntity();

					if (item.Entity is Insert insert)
					{
						Primitive refusal;
						IEnumerator<Pending> expanded = ExpandInsert(insert, item, out refusal);
						if (refusal != null)
						{
							yield return refusal;
							continue;
						}

						stack.Push(new Frame { Items = expanded });
						continue;
					}

					// A dimension's picture and a hatch's boundary are both
					// made of entities, and both used to be walked by calling
					// Map again. They go on the same explicit stack as an
					// insertion now, at one more depth, so the depth bound and
					// the entity bound apply to them and nothing recurses.
					if (item.Entity is Dimension dimension)
					{
						CheckDepth(item.Depth + 1, "DIMENSION", dimension.Handle, dimension.ObjectName);
						stack.Push(new Frame { Items = DimensionBody(dimension, item).GetEnumerator() });
						continue;
					}

					if (item.Entity is Hatch hatch)
					{
						CheckDepth(item.Depth + 1, "HATCH", hatch.Handle, hatch.ObjectName);
						stack.Push(new Frame { Items = HatchBody(hatch, item).GetEnumerator() });
						continue;
					}

					foreach (Primitive p in Map(item.Entity, item.Placement, item.Depth))
					{
						if (item.HandleOverride != 0ul)
						{
							p.ItemHandle = item.HandleOverride;
						}

						yield return Finite(p);
					}
				}
			}
			finally
			{
				while (stack.Count > 0)
				{
					stack.Pop().Items.Dispose();
				}
			}
		}

		// docs/WIRE.md's producer guarantee, applied once where the walk hands
		// a primitive to the stream.
		//
		// Nothing in a drawing has to be finite. A DWG stores a bulge and a
		// coordinate as plain doubles, and both NaN and the infinities survive
		// the round trip: g13_nan_bulge.dwg carries one of each and they
		// reached the wire, where a single NaN coordinate becomes a bounding
		// box that is NaN in every direction and a renderer that draws nothing
		// at all.
		//
		// The record is replaced rather than corrected. There is no right
		// number to put in its place, and quietly substituting one would be
		// this layer inventing geometry. The warning names the handle and the
		// rest of the drawing still crosses, which is the difference between
		// a drawing with one entity missing and a decode that failed.
		//
		// Here rather than in the encoder because here the handle is known and
		// the record is nameable. The encoder has a backstop for the same
		// condition, and that one throws: reaching it means this guard did not
		// run, which is a bug in the library rather than a fact about the
		// drawing.
		private static Primitive Finite(Primitive p)
		{
			if (p == null || !WireFormat.IsGeometry(p.Type))
			{
				return p;
			}

			double[] values = p.Values;
			for (int i = 0; i < values.Length; i++)
			{
				if (!double.IsNaN(values[i]) && !double.IsInfinity(values[i]))
				{
					continue;
				}

				Primitive w = Primitive.Warning(
					WarningCodes.NonFiniteGeometry,
					p.ItemHandle,
					RecordName.Of(p.Type)
						+ " value " + i.ToString(CultureInfo.InvariantCulture)
						+ " is " + values[i].ToString(CultureInfo.InvariantCulture)
						+ ", and docs/WIRE.md promises no geometry record carries a value "
						+ "that is not finite, so this entity is not emitted"
				);
				w.Flags = p.Flags;
				return w;
			}

			return p;
		}

		private static IEnumerable<Pending> Root(IEnumerable<Entity> roots)
		{
			Placement identity = new Placement(Identity());
			foreach (Entity e in roots)
			{
				yield return new Pending { Entity = e, Placement = identity, Depth = 0 };
			}
		}

		// ---------------------------------------------------------- INSERT

		private IEnumerator<Pending> ExpandInsert(Insert insert, Pending item, out Primitive refusal)
		{
			refusal = null;
			uint flags = item.Depth > 0 ? FlagFromBlock : 0u;

			BlockRecord block = insert.Block;
			if (block == null)
			{
				refusal = Primitive.Warning(
					WarningCodes.UnresolvedBlock,
					insert.Handle,
					"INSERT names no block record"
				);
				refusal.Flags = flags;
				return Empty();
			}

			Block header = block.BlockEntity;
			bool isXref = header != null
				&& (header.Flags.HasFlag(BlockTypeFlags.XRef)
					|| header.Flags.HasFlag(BlockTypeFlags.XRefOverlay));
			if (isXref || (header != null && header.IsUnloaded))
			{
				// An external reference is another file. This shim never
				// opens one: no network, no second read, no subprocess. The
				// reference crosses as a warning naming the path the drawing
				// recorded, and whoever wants it resolved resolves it.
				string path = header == null || string.IsNullOrEmpty(header.XRefPath)
					? block.Name
					: header.XRefPath;
				refusal = Primitive.Warning(
					WarningCodes.UnresolvedBlock,
					insert.Handle,
					"external reference " + path
						+ " is not resolved, and this decoder never resolves one"
				);
				refusal.Flags = flags;
				return Empty();
			}

			CheckDepth(item.Depth + 1, "INSERT", insert.Handle, "block " + block.Name);

			return InsertBody(insert, block, item).GetEnumerator();
		}

		private static IEnumerator<Pending> Empty()
		{
			return EmptySeq().GetEnumerator();
		}

		private static IEnumerable<Pending> EmptySeq()
		{
			yield break;
		}

		// The INSERT's own transform, built here rather than taken from
		// upstream's Insert.GetTransform().
		//
		// The same composition it makes, in the same order: the insertion's own
		// object coordinate system, the translation to the insertion point less
		// the block's base point, the rotation about z, then the scale. The one
		// difference is that the object coordinate system comes from this
		// file's arbitrary axis rather than from Matrix4.GetArbitraryAxis,
		// whose 1/64 test is integer division and never fires. For an insertion
		// with an extrusion of +Z, which is every insertion in a drawing nobody
		// has rotated out of plan, the two are identical.
		private static Matrix4 InsertMatrix(Insert insert, BlockRecord block)
		{
			XYZ basePoint = block == null || block.BlockEntity == null
				? XYZ.Zero
				: block.BlockEntity.BasePoint;

			return ObjectToWorld(insert.Normal)
				* Transform.CreateTranslation(insert.InsertPoint - basePoint).Matrix
				* Transform.CreateRotation(XYZ.AxisZ, insert.Rotation).Matrix
				* Transform.CreateScaling(
					new XYZ(insert.XScale, insert.YScale, insert.ZScale)
				).Matrix;
		}

		private IEnumerable<Pending> InsertBody(Insert insert, BlockRecord block, Pending item)
		{
			Matrix4 local = InsertMatrix(insert, block);
			int rows = insert.RowCount < 1 ? 1 : insert.RowCount;
			int cols = insert.ColumnCount < 1 ? 1 : insert.ColumnCount;

			// The attributes belong to the INSERT and are already placed in
			// the frame the INSERT sits in, so they go out under the parent
			// transform rather than under the block's.
			foreach (AttributeEntity att in insert.Attributes)
			{
				yield return new Pending
				{
					Entity = att,
					Placement = item.Placement,
					Depth = item.Depth,
				};
			}

			for (int r = 0; r < rows; r++)
			{
				for (int c = 0; c < cols; c++)
				{
					Matrix4 cell = local;
					if (r != 0 || c != 0)
					{
						double dx = c * insert.ColumnSpacing;
						double dy = r * insert.RowSpacing;
						double cosr = Math.Cos(insert.Rotation);
						double sinr = Math.Sin(insert.Rotation);
						Matrix4 offset = Transform.CreateTranslation(
							new XYZ((dx * cosr) - (dy * sinr), (dx * sinr) + (dy * cosr), 0.0)
						).Matrix;
						cell = Compose(offset, local);
					}

					// One placement per cell of the array, shared by every
					// entity the cell holds, so the basis behind it is
					// measured at most once however many entities that is.
					Placement composed = new Placement(Compose(item.Placement.Matrix, cell));
					foreach (Entity e in block.Entities)
					{
						yield return new Pending
						{
							Entity = e,
							Placement = composed,
							Depth = item.Depth + 1,
						};
					}
				}
			}
		}

		// --------------------------------------------------------- mapping

		public static Primitive ReaderNotification(string message)
		{
			return Primitive.Warning(WarningCodes.ReaderNotification, 0ul, message);
		}

		private IEnumerable<Primitive> Map(Entity e, Placement place, int depth)
		{
			ulong h = e.Handle;
			uint flags = depth > 0 ? FlagFromBlock : 0u;

			// The basis is read inside the arms that use it and nowhere else.
			// Half these arms never look at it, and measuring for them was six
			// transform applications, three square roots and an atan2 spent on
			// an answer nothing read.
			switch (e)
			{
				case Line line:
				{
					XYZ a = place.Apply(line.StartPoint);
					XYZ b = place.Apply(line.EndPoint);
					yield return Primitive.Line(h, flags, a.X, a.Y, a.Z, b.X, b.Y, b.Z);
					yield break;
				}

				// Arc before Circle: ACadSharp's Arc derives from Circle.
				//
				// This used to say the other order silently turns every arc
				// into a full circle. It does not: a subtype after its base is
				// CS8120, the arm is unreachable, and the build fails. The
				// order of the arms that exist is the one thing the compiler
				// does hold. What it cannot see is a kind with no arm at all,
				// which is what POLYFACE_MESH and POLYGON_MESH were until the
				// two arms below, and what test_shim_source_rules.py reads
				// this switch as text to check.
				case Arc arc:
				{
					// The centre is in the arc's own plane and the angles are
					// counter-clockwise about its normal, so the whole
					// correction is the placement with that plane composed into
					// it. Turn carries the rotation and Mirrored carries the
					// reflection, and neither is a special case of the other.
					//
					// The gate is Uniform rather than the old IsSimilarity,
					// which excluded a mirror. A reflection preserves every
					// shape exactly; it was only worth warning about while the
					// angles below did not follow it.
					Placement ocs = place.WithOcs(arc.Normal, 0.0);
					Basis basis = ocs.Basis;
					if (!basis.Uniform)
					{
						yield return NonUniform(h, flags, "ARC");
					}

					XYZ c = ocs.Apply(arc.Center);

					// Under a reflection the image of the sweep runs the other
					// way round the circle, so the record's two ends swap. The
					// sweep is preserved: end minus start is still the included
					// angle the drawing has. What a mirror cannot preserve is
					// which end the drawing called the start, and no record
					// whose angles are counter-clockwise about its normal can
					// say that.
					double start = basis.Mirrored
						? basis.Turn - arc.EndAngle
						: arc.StartAngle + basis.Turn;
					double end = basis.Mirrored
						? basis.Turn - arc.StartAngle
						: arc.EndAngle + basis.Turn;

					yield return Primitive.Arc(
						h,
						flags,
						c.X,
						c.Y,
						c.Z,
						arc.Radius * basis.ScaleX,
						start,
						end,
						basis.Normal.X,
						basis.Normal.Y,
						basis.Normal.Z
					);
					yield break;
				}

				case Circle circle:
				{
					// The same lift, and no angle correction to make: a circle
					// has no angles, which is why it was the one of the three
					// that a reflection was already getting right.
					Placement ocs = place.WithOcs(circle.Normal, 0.0);
					Basis basis = ocs.Basis;
					if (!basis.Uniform)
					{
						yield return NonUniform(h, flags, "CIRCLE");
					}

					XYZ c = ocs.Apply(circle.Center);
					yield return Primitive.Circle(
						h,
						flags,
						c.X,
						c.Y,
						c.Z,
						circle.Radius * basis.ScaleX,
						basis.Normal.X,
						basis.Normal.Y,
						basis.Normal.Z
					);
					yield break;
				}

				case Ellipse ellipse:
				{
					// An ELLIPSE is the one curve DXF stores in world
					// coordinates already, so there is no lift here. Its two
					// parameters are measured from the major axis, which is on
					// the record, so there is no Turn either: the frame the
					// parameters live in is carried rather than implied, and
					// the only thing a transform can do to them is reverse
					// them.
					XYZ tc = place.Apply(ellipse.Center);
					XYZ tm = place.Apply(ellipse.Center + ellipse.MajorAxisEndPoint) - tc;

					XYZ major = Unit(ellipse.MajorAxisEndPoint);
					Basis basis = place.BasisOf(
						major,
						XYZ.Cross(Unit(ellipse.Normal), major)
					);
					if (!basis.Uniform)
					{
						yield return NonUniform(h, flags, "ELLIPSE");
					}

					yield return Primitive.Ellipse(
						h,
						flags,
						tc.X,
						tc.Y,
						tc.Z,
						tm.X,
						tm.Y,
						tm.Z,
						ellipse.RadiusRatio,
						basis.Mirrored ? -ellipse.EndParameter : ellipse.StartParameter,
						basis.Mirrored ? -ellipse.StartParameter : ellipse.EndParameter,
						basis.Normal.X,
						basis.Normal.Y,
						basis.Normal.Z
					);
					yield break;
				}

				case Spline spline:
				{
					// A spline's control points are affine, so any transform
					// this walk can apply lands exactly on the transformed
					// control points. Knots and weights are untouched by
					// construction.
					// Counted before anything is allocated. The encoder's own
					// guard never ran for a spline, and by the time it would
					// have, the control points, the knots and the weights are
					// already a list the library asked the allocator for.
					CheckPointCount(spline.ControlPoints.Count, "a Spline record's control points");
					CheckPointCount(spline.Knots.Count, "a Spline record's knots");

					// Straight into an array of the size the count already
					// gives, rather than a List that is grown and then copied
					// out. A spline's control points used to be copied four
					// times between the document and the wire.
					double[] ctrl = new double[spline.ControlPoints.Count * 3];
					int at = 0;
					foreach (XYZ p in spline.ControlPoints)
					{
						XYZ v = place.Apply(p);
						ctrl[at] = v.X;
						ctrl[at + 1] = v.Y;
						ctrl[at + 2] = v.Z;
						at += 3;
					}

					yield return Primitive.Spline(
						h,
						flags,
						(uint)spline.Degree,
						(uint)spline.Flags,
						spline.Knots.ToArray(),
						ctrl,
						spline.Weights.ToArray()
					);
					yield break;
				}

				case LwPolyline lw:
				{
					CheckPointCount(lw.Vertices.Count, "a Polyline record");

					// A vertex is two numbers in the plane the normal defines
					// and the elevation is its third, so all three go through
					// the lift together. Lifting the pair and leaving the
					// elevation behind is the half-fix that puts a flat
					// polyline on the wrong side of its own plane.
					Placement ocs = place.WithOcs(lw.Normal, 0.0);
					double[] pts = new double[lw.Vertices.Count * 3];
					double[] lwBulges = new double[lw.Vertices.Count];
					bool lwAnyBulge = false;
					for (int i = 0; i < lw.Vertices.Count; i++)
					{
						LwPolyline.Vertex v = lw.Vertices[i];
						XYZ at = ocs.Apply(
							new XYZ(v.Location.X, v.Location.Y, lw.Elevation)
						);
						pts[i * 3] = at.X;
						pts[(i * 3) + 1] = at.Y;
						pts[(i * 3) + 2] = at.Z;
						lwBulges[i] = v.Bulge;
						lwAnyBulge |= v.Bulge != 0.0;
					}

					Basis basis = ocs.Basis;
					if (!basis.Uniform && lwAnyBulge)
					{
						yield return NonUniform(h, flags, "LWPOLYLINE");
					}

					yield return OnePolyline(
						h,
						flags,
						lw.IsClosed,
						pts,
						lwBulges,
						basis.Mirrored,
						basis.Normal
					);
					yield break;
				}

				// PolyfaceMesh and PolygonMesh before IPolyline. Both derive
				// from Polyline<T>, Polyline<T> implements IPolyline, so both
				// used to reach the arm below and go out as one open polyline
				// threaded through their own vertices: a wandering line
				// through real coordinates, no warning, and byte-identical in
				// shape to the polylines beside it. A consumer holding that
				// record has no way to know it is not a polyline, which is the
				// one outcome docs/WIRE.md's contract exists to prevent
				// (libviprs-dep#82).
				//
				// Neither is a polyline. A polyface mesh is a set of faces
				// indexed into a vertex list, kept in its own Faces collection
				// here, and a polygon mesh is an M by N grid. Emitting the
				// faces and the grid is a feature and it is not this change;
				// refusing is what turns silent wrong geometry into something
				// a consumer is told about.
				//
				// The subclass marker is what tells the two apart, because all
				// four kinds under Polyline<T> are the entity POLYLINE in DXF
				// and ObjectName says so for every one of them: refusing on
				// ObjectName would say POLYLINE and a census of what this build
				// refuses would gain a row naming neither kind.
				//
				// What goes in the message is the kind, not the marker.
				// MarkerKind is the whole of that translation and the comment
				// on it is the argument.
				case PolyfaceMesh mesh:
				{
					yield return Refused(h, flags, MarkerKind(mesh.SubclassMarker));
					yield break;
				}

				case PolygonMesh grid:
				{
					yield return Refused(h, flags, MarkerKind(grid.SubclassMarker));
					yield break;
				}

				case IPolyline poly:
				{
					// POLYLINE's 3D flag is exactly the flag that says its
					// vertices are world coordinates: a 2D one stores them in
					// the plane its normal defines with the elevation as the
					// third, and a 3D one does not and need not even be planar.
					// So the plane and the points come from two placements
					// here. The plane is still the entity's, because that is
					// what a bulge's sign is measured about and what the record
					// is telling a consumer; the points of a 3D one are already
					// where they belong and lifting them would move them.
					Placement plane = place.WithOcs(poly.Normal, 0.0);
					Placement at = poly is Polyline3D ? place : plane;

					List<double> pts = new List<double>();
					List<double> bulges = new List<double>();
					bool anyBulge = false;
					foreach (IVertex v in poly.Vertices)
					{
						CheckPointCount(bulges.Count + 1, "a Polyline record");
						IVector loc = v.Location;
						double x = loc.Dimension > 0 ? loc[0] : 0.0;
						double y = loc.Dimension > 1 ? loc[1] : 0.0;
						double z = loc.Dimension > 2 ? loc[2] : poly.Elevation;
						Append(pts, at, new XYZ(x, y, z));
						bulges.Add(v.Bulge);
						anyBulge |= v.Bulge != 0.0;
					}

					Basis basis = plane.Basis;
					if (!basis.Uniform && anyBulge)
					{
						yield return NonUniform(h, flags, "POLYLINE");
					}

					yield return OnePolyline(
						h,
						flags,
						poly.IsClosed,
						pts.ToArray(),
						bulges.ToArray(),
						basis.Mirrored,
						basis.Normal
					);
					yield break;
				}

				case MText mtext:
				{
					// MTEXT's insertion point is in world coordinates, unlike
					// TEXT's below, so there is no lift on this arm.
					Basis basis = place.Basis;
					XYZ p = place.Apply(mtext.InsertPoint);
					yield return Primitive.TextAt(
						h,
						flags,
						p.X,
						p.Y,
						p.Z,
						mtext.Height * basis.ScaleY,
						mtext.Rotation + basis.Turn,
						mtext.Value ?? string.Empty
					);
					yield break;
				}

				// AttributeEntity and AttributeDefinition derive from
				// TextEntity, so this arm carries the text a block instance
				// actually shows.
				case TextEntity text:
				{
					// TEXT's insertion point is in the plane its normal
					// defines, so it is lifted the way an arc's centre is.
					//
					// The rotation is not, and cannot be: record 10 carries no
					// normal, so there is nowhere to tell a consumer which
					// plane the angle is measured in, and the in-plane angle is
					// the closest this version gets. Giving Text a normal is a
					// wire change and is not this one. Lifting the point is
					// still right on its own: an unlifted one is not in the
					// drawing at all.
					Placement ocs = place.WithOcs(text.Normal, 0.0);
					Basis basis = ocs.Basis;
					XYZ p = ocs.Apply(text.InsertPoint);
					yield return Primitive.TextAt(
						h,
						flags,
						p.X,
						p.Y,
						p.Z,
						text.Height * basis.ScaleY,
						text.Rotation + basis.Turn,
						text.Value ?? string.Empty
					);
					yield break;
				}

				// SOLID, MESH and 3DFACE. All three are a filled face given by its
				// corners and all three lower to record 9, so their bodies sit
				// together in Flatten.Faces.cs; only the labels are here, because
				// the order of the labels is what the compiler checks.
				//
				// 3DFACE is beside them and shares nothing with SOLID but the
				// record. It carries the same four corner properties at the same
				// DXF codes and is a different entity in both of the ways that
				// decide what a Polygon says: its per-edge InvisibleEdgeFlags only
				// read as traversal order, so its corners go out 1, 2, 3, 4 rather
				// than SOLID's 1, 2, 4, 3, and Face3D carries no normal at all and
				// documents every corner as world, so it is neither lifted through
				// an OCS nor entitled to the placement's normal. It is also the
				// only one of the three that can ask for something record 9 has no
				// field for, which is warning 113. Flatten.Faces.cs records the
				// rest.
				case Solid solid:
				{
					yield return SolidPolygon(solid, h, flags, place);
					yield break;
				}

				case Mesh mesh:
				{
					foreach (Primitive p in MeshPolygons(mesh, h, flags, place))
					{
						yield return p;
					}

					yield break;
				}

				case Face3D face:
				{
					foreach (Primitive p in Face3DPolygon(face, h, flags, place))
					{
						yield return p;
					}

					yield break;
				}

				// LEADER. Its vertices are already world coordinates and its
				// arrowhead is not in the drawing at all, so the body and the
				// two sentences that say why are in Flatten.Leaders.cs.
				case Leader leader:
				{
					foreach (Primitive p in LeaderPolyline(leader, h, flags, place))
					{
						yield return p;
					}

					yield break;
				}

				// DIMENSION and HATCH are not here. They are composites: they
				// expand into other entities, and expanding them from inside
				// Map is what put a file-controlled recursion on the CLR
				// stack. Walk dispatches them onto its own stack instead.

				// Two different things end up below, and until RefusedKinds
				// existed they left the identical warning.
				//
				// A kind in that table is one somebody looked at and decided
				// against: the geometry is not in the drawing (SHAPE, IMAGE,
				// PDFUNDERLAY), or it is in a form nothing here evaluates
				// (3DSOLID, REGION), or no record in wire 2 can hold it (RAY,
				// XLINE). Those get code 109 and a sentence saying which.
				// Everything else is a gap nobody has got to, which is what 100
				// has always meant, and the difference is the whole reason a
				// consumer has a second number to branch on.
				//
				// docs/adr/0002-what-this-decoder-refuses.md is the decision
				// and carries what would reopen each row.
				default:
				{
					yield return Refused(h, flags, e.ObjectName);
					yield break;
				}
			}
		}

		// A polyline, exactly as wire version 2 carries it: one record,
		// whatever its bulges are.
		//
		// docs/WIRE.md's Polyline has a slot for every vertex's bulge, so
		// nothing here has a decision to make. What used to be here did: it
		// split a bulged polyline into straight runs and one Arc per bulged
		// span, and that lost three things a consumer cannot get back.
		// Closed-ness, because a run of Polyline records has nowhere to say
		// the path closes. Instance identity, because under block expansion
		// every instance of a block carries the block entity's own handle, so
		// three insertions of one slot arrived as twelve records under one
		// handle with no delimiter anywhere. And accuracy, because a centre
		// and two angles reconstructed from a small bulge drift from the
		// vertices either side of them: at 1e-12 an endpoint is already 2.6e-5
		// chord lengths out, and every arc-to-polyline conversion in every CAD
		// tool leaves bulges of about 1e-15 behind.
		//
		// One record per entity, in walk order, and all three come back free.
		private static Primitive OnePolyline(
			ulong handle,
			uint flags,
			bool closed,
			double[] points,
			double[] bulges,
			bool mirrored,
			XYZ normal
		)
		{
			return Primitive.Polyline(
				handle,
				flags,
				closed,
				points,
				BulgeArray(points.Length / 3, bulges, mirrored),
				normal.X,
				normal.Y,
				normal.Z
			);
		}

		// The bulge array a record carries, or null when it carries none.
		//
		// The array is left out when every span is straight, which keeps a
		// plain polyline the size it was. This is the one place `== 0.0` on a
		// double belongs in this file: it is a size decision, not a geometry
		// one. A bulge of 1e-300 is emitted exactly as read, because calling
		// it straight is a tolerance decision and the consumer is the only
		// layer that knows its tolerance.
		private static double[] BulgeArray(int n, double[] bulges, bool mirrored)
		{
			double[] b = null;
			for (int i = 0; i < n && i < bulges.Length; i++)
			{
				if (bulges[i] != 0.0)
				{
					b = new double[n];
					break;
				}
			}

			if (b != null)
			{
				for (int i = 0; i < n; i++)
				{
					double v = i < bulges.Length ? bulges[i] : 0.0;

					// A reflection flips which side of the chord the arc
					// bulges to. The vertices follow the transform and the
					// bulge does not, so negating it is the whole of the
					// correction, and it is exact: no tolerance, no centre,
					// nothing reconstructed. The `v != 0.0` guard only keeps a
					// straight span from crossing as negative zero.
					b[i] = mirrored && v != 0.0 ? -v : v;
				}
			}

			return b;
		}

		// Record 9 takes record 4's payload, so it takes the same bulge array
		// and the same mirror correction. A hatch loop of lines and circular
		// arcs is one closed polygon with a bulge on the arc spans.
		private static Primitive OnePolygon(
			ulong handle,
			uint flags,
			double[] points,
			double[] bulges,
			bool mirrored,
			XYZ normal
		)
		{
			return Primitive.Polygon(
				handle,
				flags,
				points,
				BulgeArray(points.Length / 3, bulges, mirrored),
				normal.X,
				normal.Y,
				normal.Z
			);
		}

		// An entity kind this version does not flatten, named by whatever the
		// caller has that identifies it in the source format.
		//
		// One refusal path, and it asks RefusedKinds first.
		//
		// The sentence below used to be written out in three places: here, in
		// the switch's default arm, which also carried its own copy of the
		// table lookup, and as a variant inside RefusedKinds' WIPEOUT row.
		// That row keeps its variant, because it is a different sentence
		// saying a different thing. The other two are now one.
		//
		// The lookup is the half that mattered. This method hardcoded code 100,
		// so an arm refusing a kind on its own, which is what the two mesh arms
		// above do, took 100 whatever the table said: a later decision to put a
		// mesh on 109 would have landed in RefusedKinds, been read by the
		// default arm no mesh reaches, and been ignored by the two arms that
		// actually refuse one.
		private static Primitive Refused(ulong handle, uint flags, string what)
		{
			uint code;
			string reason;
			if (!RefusedKinds.TryGet(what, out code, out reason))
			{
				code = WarningCodes.UnsupportedEntity;
				reason = what + " is not a primitive this version flattens";
			}

			Primitive w = Primitive.Warning(code, handle, reason);
			w.Flags = flags;
			return w;
		}

		// The DXF kind a POLYLINE's subclass marker names.
		//
		// SubclassMarker stays the discriminator, because ObjectName is
		// POLYLINE for all four kinds under Polyline<T> and cannot tell them
		// apart. What it is not is a name anything else on this wire speaks.
		// The refusal census of real_AC1032 reads POINT, MULTILEADER, MLINE,
		// TOLERANCE, 3DSOLID, 3DFACE, LEADER, IMAGE, PDFUNDERLAY, RAY, REGION,
		// SHAPE, WIPEOUT, XLINE and then AcDbPolyFaceMesh: fourteen DXF entity
		// names and one class name, and the odd one out is the kind that got
		// its own arm. docs/WIRE.md's row for code 100 says the message names
		// the source format's type, and tests/test_refusal_decisions.py holds
		// the table next door to the same rule because the corpus tests read
		// the type back by splitting the message on its first space.
		//
		// Against upstream's own constants rather than the two literals, so a
		// marker whose spelling moves under a pin bump moves this with it and
		// a constant that goes away is a build error rather than a census that
		// quietly grows a class name back.
		private static string MarkerKind(string marker)
		{
			switch (marker)
			{
				case ACadSharp.DxfSubclassMarker.PolyfaceMesh: return "POLYFACE_MESH";
				case ACadSharp.DxfSubclassMarker.PolygonMesh: return "POLYGON_MESH";
				default: return marker;
			}
		}

		// What the warning says, which used to be false on the input it was
		// most likely to be read on.
		//
		// It read "a block transform that is not a similarity, so its
		// parameters describe a shape the transform does not preserve". A
		// reflection is not a similarity and it preserves every shape exactly;
		// what it does not preserve is handedness, and every record that has a
		// handedness now follows it. What is left is the case the code name has
		// always claimed: a transform that scales the entity's plane by
		// different amounts in different directions, under which a circle is an
		// ellipse and a bulge is an elliptical arc.
		private static Primitive NonUniform(ulong handle, uint flags, string what)
		{
			Primitive w = Primitive.Warning(
				WarningCodes.NonUniformBlockScale,
				handle,
				what + " crossed under a block transform that does not scale its plane "
					+ "uniformly, so the shape its parameters describe is stretched in "
					+ "the drawing and they no longer name it. A reflection is not this "
					+ "case and raises nothing: a mirror is exact"
			);
			w.Flags = flags;
			return w;
		}

		// -------------------------------------------------------- DIMENSION

		// A dimension's picture, as items for the walk's own stack.
		//
		// This used to call Map on every entity in the block, which recursed
		// whenever one of them was itself a dimension and never looked at
		// max_block_depth. A file with a chain of nested dimensions took the
		// process down with a stack overflow the export could not catch, and
		// two dimensions whose blocks hold each other did it without needing
		// to be deep. Handing the entities back instead means the walk applies
		// the same depth bound and the same entity count it applies to an
		// insertion.
		private IEnumerable<Pending> DimensionBody(Dimension dim, Pending item)
		{
			BlockRecord block = dim.Block;
			int handed = 0;
			if (block != null)
			{
				foreach (Entity e in block.Entities)
				{
					// Never through an INSERT: a dimension block is generated
					// geometry, not a user block, and walking it as a block
					// would hand it a second depth budget.
					if (e is Insert)
					{
						continue;
					}

					handed++;
					yield return new Pending
					{
						Entity = e,
						Placement = item.Placement,
						Depth = item.Depth + 1,
					};
				}
			}

			if (handed == 0)
			{
				Primitive w = Primitive.Warning(
					WarningCodes.DimensionWithoutBlock,
					dim.Handle,
					dim.ObjectName + " carries no block geometry, so there is nothing to draw"
				);
				w.Flags = item.Depth > 0 ? FlagFromBlock : 0u;
				yield return new Pending { Record = w, Depth = item.Depth };
			}
		}

		// ------------------------------------------------------------ HATCH

		// A hatch's boundary, as items for the walk's own stack: finished
		// records where a loop is a polygon, and entities where it is not.
		private IEnumerable<Pending> HatchBody(Hatch hatch, Pending item)
		{
			ulong h = hatch.Handle;
			uint flags = item.Depth > 0 ? FlagFromBlock : 0u;

			// A hatch's boundary is in the plane its normal and elevation
			// define, and so is every edge upstream hands back from
			// Edge.ToEntity(), which builds one with a z of zero and a normal
			// of +Z whatever the hatch says. Composing the hatch's own system
			// into the placement once puts both paths in the right plane at the
			// same time: the polygon's vertices go through it, and so does
			// every edge entity the not-a-polygon path emits as a record of its
			// own, because those go back onto the walk carrying this placement.
			Placement place = item.Placement.WithOcs(hatch.Normal, hatch.Elevation);
			int loops = 0;

			foreach (Hatch.BoundaryPath path in hatch.Paths)
			{
				if (path.Edges.Count == 0)
				{
					continue;
				}

				loops++;
				double[] pts;
				double[] bulges;
				if (TryPolygon(path, place, out pts, out bulges))
				{
					CheckPointCount(pts.Length / 3, "a Polygon record");

					// A bulged boundary under a non-uniform scale is an
					// elliptical arc, exactly as a bulged polyline is, so it
					// gets the same warning. A straight loop is not: a
					// polygon's vertices transform exactly whatever the scale.
					bool anyBulge = false;
					foreach (double b in bulges)
					{
						anyBulge |= b != 0.0;
					}

					Basis basis = place.Basis;
					if (!basis.Uniform && anyBulge)
					{
						Primitive squashed = NonUniform(h, flags, "HATCH");
						yield return new Pending { Record = squashed, Depth = item.Depth };
					}

					yield return new Pending
					{
						Record = OnePolygon(h, flags, pts, bulges, basis.Mirrored, basis.Normal),
						Depth = item.Depth,
					};
					continue;
				}

				// An ellipse or spline edge cannot be a bulge, and turning one
				// into a bulge would be tessellation by another name. The loop
				// goes out as its own edges instead, which is strictly more
				// than a polygon carries.
				Primitive w = Primitive.Warning(
					WarningCodes.HatchLoopNotPolygon,
					h,
					"boundary loop " + loops.ToString(CultureInfo.InvariantCulture)
						+ " carries a curve a closed polygon cannot express, so its edges "
						+ "follow as records"
				);
				w.Flags = flags;
				yield return new Pending { Record = w, Depth = item.Depth };

				foreach (Hatch.BoundaryPath.Edge edge in path.Edges)
				{
					yield return new Pending
					{
						Entity = edge.ToEntity(),
						Placement = place,
						Depth = item.Depth + 1,
						HandleOverride = h,
					};
				}
			}

			if (loops != 0)
			{
				yield break;
			}

			// Pattern only. ACadSharp's own ExplodePattern clips the pattern to
			// the boundary and returns nothing when there is no boundary, so on
			// the pinned version this always reaches the warning. The call is
			// still here because a later upstream that can produce unbounded
			// pattern lines should reach the Line arm rather than silently
			// change shape.
			int lines = 0;
			foreach (Entity e in hatch.ExplodePattern())
			{
				if (!(e is Line))
				{
					continue;
				}

				lines++;
				yield return new Pending
				{
					Entity = e,
					Placement = place,
					Depth = item.Depth + 1,
					HandleOverride = h,
				};
			}

			if (lines == 0)
			{
				Primitive w = Primitive.Warning(
					WarningCodes.HatchPatternOnly,
					h,
					"HATCH has no boundary loop, and its pattern produced no geometry to draw"
				);
				w.Flags = flags;
				yield return new Pending { Record = w, Depth = item.Depth };
			}
		}

		// A boundary loop becomes one closed polygon when every edge is a
		// straight span or a circular arc, because wire version 2's Polygon
		// carries a bulge per vertex and a bulge is exactly a circular arc.
		// An ellipse or a spline edge still returns false and the caller emits
		// the edges as their own records, which keeps every parameter rather
		// than straightening it.
		//
		// The arc conversion is the one the shim does keep, and it runs in the
		// direction that is well conditioned: from a sweep to tan(sweep / 4),
		// never from a bulge to a centre. It goes through ACadSharp's own
		// ToEntity, which is the same object the not-a-polygon path emits as
		// an Arc record, so the two paths cannot disagree about what the edge
		// means.
		private static bool TryPolygon(
			Hatch.BoundaryPath path,
			Placement place,
			out double[] points,
			out double[] bulges
		)
		{
			List<double> pts = new List<double>();
			List<double> bs = new List<double>();
			points = null;
			bulges = null;

			foreach (Hatch.BoundaryPath.Edge edge in path.Edges)
			{
				switch (edge)
				{
					case Hatch.BoundaryPath.Line line:
						Append(pts, place, new XYZ(line.Start.X, line.Start.Y, 0.0));
						bs.Add(0.0);
						break;

					case Hatch.BoundaryPath.Arc arc:
					{
						double bulge;
						XY start;
						if (!ArcEdge(arc, out start, out bulge))
						{
							return false;
						}

						Append(pts, place, new XYZ(start.X, start.Y, 0.0));
						bs.Add(bulge);
						break;
					}

					case Hatch.BoundaryPath.Polyline poly:
					{
						// The bulge lives in the vertex's Z component, which is
						// upstream's own storage and not a coordinate.
						foreach (XYZ v in poly.Vertices)
						{
							Append(pts, place, new XYZ(v.X, v.Y, 0.0));
							bs.Add(v.Z);
						}

						break;
					}

					default:
						return false;
				}
			}

			if (pts.Count == 0)
			{
				return false;
			}

			points = pts.ToArray();
			bulges = bs.ToArray();
			return true;
		}

		// One circular-arc edge as the vertex the loop enters it at and the
		// bulge of the span leaving that vertex.
		//
		// A boundary arc is described by a centre, a radius, two angles and a
		// direction flag, and the flag is not a sign on the sweep: upstream's
		// ToEntity reads a clockwise edge as the arc from 2*pi - end to
		// 2*pi - start, so the edge's own first point is the entity's last.
		// Taking the entity and reading its endpoints back is what keeps this
		// agreeing with the path that emits the same edge as an Arc record.
		//
		// A full circle is refused. One span cannot carry it: the included
		// angle is 2*pi, a quarter of that is pi/2, and tan(pi/2) is not a
		// number. Such a loop goes out as its own edges, the way an ellipse
		// edge does.
		private static bool ArcEdge(Hatch.BoundaryPath.Arc edge, out XY start, out double bulge)
		{
			start = default(XY);
			bulge = 0.0;

			ACadSharp.Entities.Arc entity = edge.ToEntity() as ACadSharp.Entities.Arc;
			if (entity == null)
			{
				return false;
			}

			double sweep = entity.EndAngle - entity.StartAngle;
			double twoPi = 2.0 * Math.PI;
			sweep = sweep - (twoPi * Math.Floor(sweep / twoPi));
			if (sweep <= Eps || sweep >= twoPi - Eps)
			{
				return false;
			}

			XY first = new XY(
				entity.Center.X + (entity.Radius * Math.Cos(entity.StartAngle)),
				entity.Center.Y + (entity.Radius * Math.Sin(entity.StartAngle))
			);
			XY last = new XY(
				entity.Center.X + (entity.Radius * Math.Cos(entity.EndAngle)),
				entity.Center.Y + (entity.Radius * Math.Sin(entity.EndAngle))
			);

			start = edge.CounterClockWise ? first : last;
			bulge = Math.Tan(sweep / 4.0);
			if (!edge.CounterClockWise)
			{
				bulge = -bulge;
			}

			return true;
		}
	}
}
