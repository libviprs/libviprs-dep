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
	internal sealed class Flattener
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
			public Transform Transform;
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

		private static double[] Xyz(Transform t, XYZ p)
		{
			XYZ v = t.ApplyTransform(p);
			return new double[] { v.X, v.Y, v.Z };
		}

		private static void Append(List<double> into, Transform t, XYZ p)
		{
			XYZ v = t.ApplyTransform(p);
			into.Add(v.X);
			into.Add(v.Y);
			into.Add(v.Z);
		}

		// The in-plane scale factors and rotation a transform applies,
		// measured rather than decomposed. A composed chain of insertions is
		// not guaranteed to decompose into the translate, rotate and scale it
		// was built from, and a measurement of the basis always is.
		private struct Basis
		{
			public double ScaleX;
			public double ScaleY;
			public double Rotation;
			public bool Mirrored;

			// Equal scale factors, mirror or no mirror. This is what a
			// polyline needs: under a reflection its vertices transform and
			// its bulges negate, and the result is exactly the shape the
			// drawing has. Nothing is approximated, so there is nothing to
			// warn about.
			public bool IsUniform
			{
				get
				{
					double m = Math.Max(Math.Max(ScaleX, ScaleY), 1.0);
					return Math.Abs(ScaleX - ScaleY) <= 1e-9 * m;
				}
			}

			// Uniform and orientation-preserving. Arc, Circle and Ellipse need
			// this stronger one: they cross as a centre and angles measured
			// counter-clockwise, and a reflection flips what counter-clockwise
			// means without any field of theirs following it.
			public bool IsSimilarity
			{
				get { return !Mirrored && IsUniform; }
			}
		}

		private static Basis MeasureBasis(Transform t)
		{
			XYZ o = t.ApplyTransform(XYZ.Zero);
			XYZ x = t.ApplyTransform(XYZ.AxisX) - o;
			XYZ y = t.ApplyTransform(XYZ.AxisY) - o;
			Basis b = new Basis();
			b.ScaleX = x.GetLength();
			b.ScaleY = y.GetLength();
			b.Rotation = Math.Atan2(x.Y, x.X);
			b.Mirrored = (x.X * y.Y) - (x.Y * y.X) < 0.0;
			return b;
		}

		private static bool IsIdentityish(Transform t)
		{
			XYZ o = t.ApplyTransform(XYZ.Zero);
			XYZ x = t.ApplyTransform(XYZ.AxisX) - o;
			XYZ y = t.ApplyTransform(XYZ.AxisY) - o;
			return o.GetLength() < Eps
				&& Math.Abs(x.X - 1.0) < Eps && Math.Abs(x.Y) < Eps
				&& Math.Abs(y.Y - 1.0) < Eps && Math.Abs(y.X) < Eps;
		}

		private static Transform Identity()
		{
			return new Transform(Matrix4.Identity);
		}

		private static Transform Compose(Transform outer, Transform inner)
		{
			return new Transform(outer.Matrix * inner.Matrix);
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
						yield return item.Record;
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

					foreach (Primitive p in Map(item.Entity, item.Transform, item.Depth))
					{
						if (item.HandleOverride != 0ul)
						{
							p.ItemHandle = item.HandleOverride;
						}

						yield return p;
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

		private static IEnumerable<Pending> Root(IEnumerable<Entity> roots)
		{
			Transform identity = Identity();
			foreach (Entity e in roots)
			{
				yield return new Pending { Entity = e, Transform = identity, Depth = 0 };
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

		private IEnumerable<Pending> InsertBody(Insert insert, BlockRecord block, Pending item)
		{
			Transform local = insert.GetTransform();
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
					Transform = item.Transform,
					Depth = item.Depth,
				};
			}

			for (int r = 0; r < rows; r++)
			{
				for (int c = 0; c < cols; c++)
				{
					Transform cell = local;
					if (r != 0 || c != 0)
					{
						double dx = c * insert.ColumnSpacing;
						double dy = r * insert.RowSpacing;
						double cosr = Math.Cos(insert.Rotation);
						double sinr = Math.Sin(insert.Rotation);
						Transform offset = Transform.CreateTranslation(
							new XYZ((dx * cosr) - (dy * sinr), (dx * sinr) + (dy * cosr), 0.0)
						);
						cell = Compose(offset, local);
					}

					Transform composed = Compose(item.Transform, cell);
					foreach (Entity e in block.Entities)
					{
						yield return new Pending
						{
							Entity = e,
							Transform = composed,
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

		private IEnumerable<Primitive> Map(Entity e, Transform t, int depth)
		{
			ulong h = e.Handle;
			uint flags = depth > 0 ? FlagFromBlock : 0u;
			Basis basis = MeasureBasis(t);
			bool identity = IsIdentityish(t);

			switch (e)
			{
				case Line line:
				{
					double[] a = Xyz(t, line.StartPoint);
					double[] b = Xyz(t, line.EndPoint);
					yield return Primitive.Line(h, flags, a[0], a[1], a[2], b[0], b[1], b[2]);
					yield break;
				}

				// Arc before Circle: ACadSharp's Arc derives from Circle, so
				// the other order silently turns every arc into a full circle.
				case Arc arc:
				{
					if (!identity && !basis.IsSimilarity)
					{
						yield return NonUniform(h, flags, "ARC");
					}

					double[] c = Xyz(t, arc.Center);
					double turn = identity ? 0.0 : basis.Rotation;
					yield return Primitive.Arc(
						h,
						flags,
						c[0],
						c[1],
						c[2],
						arc.Radius * basis.ScaleX,
						arc.StartAngle + turn,
						arc.EndAngle + turn,
						arc.Normal.X,
						arc.Normal.Y,
						arc.Normal.Z
					);
					yield break;
				}

				case Circle circle:
				{
					if (!identity && !basis.IsSimilarity)
					{
						yield return NonUniform(h, flags, "CIRCLE");
					}

					double[] c = Xyz(t, circle.Center);
					yield return Primitive.Circle(
						h,
						flags,
						c[0],
						c[1],
						c[2],
						circle.Radius * basis.ScaleX,
						circle.Normal.X,
						circle.Normal.Y,
						circle.Normal.Z
					);
					yield break;
				}

				case Ellipse ellipse:
				{
					if (!identity && !basis.IsSimilarity)
					{
						yield return NonUniform(h, flags, "ELLIPSE");
					}

					XYZ tc = t.ApplyTransform(ellipse.Center);
					XYZ tm = t.ApplyTransform(ellipse.Center + ellipse.MajorAxisEndPoint) - tc;
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
						ellipse.StartParameter,
						ellipse.EndParameter,
						ellipse.Normal.X,
						ellipse.Normal.Y,
						ellipse.Normal.Z
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

					List<double> ctrl = new List<double>(spline.ControlPoints.Count * 3);
					foreach (XYZ p in spline.ControlPoints)
					{
						Append(ctrl, t, p);
					}

					yield return Primitive.Spline(
						h,
						flags,
						(uint)spline.Degree,
						(uint)spline.Flags,
						spline.Knots.ToArray(),
						ctrl.ToArray(),
						spline.Weights.ToArray()
					);
					yield break;
				}

				case LwPolyline lw:
				{
					CheckPointCount(lw.Vertices.Count, "a Polyline record");
					List<double> pts = new List<double>(lw.Vertices.Count * 3);
					double[] lwBulges = new double[lw.Vertices.Count];
					bool lwAnyBulge = false;
					for (int i = 0; i < lw.Vertices.Count; i++)
					{
						LwPolyline.Vertex v = lw.Vertices[i];
						Append(pts, t, new XYZ(v.Location.X, v.Location.Y, lw.Elevation));
						lwBulges[i] = v.Bulge;
						lwAnyBulge |= v.Bulge != 0.0;
					}

					if (!identity && !basis.IsUniform && lwAnyBulge)
					{
						yield return NonUniform(h, flags, "LWPOLYLINE");
					}

					yield return OnePolyline(
						h,
						flags,
						lw.IsClosed,
						pts.ToArray(),
						lwBulges,
						basis.Mirrored,
						lw.Normal
					);
					yield break;
				}

				case IPolyline poly:
				{
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
						Append(pts, t, new XYZ(x, y, z));
						bulges.Add(v.Bulge);
						anyBulge |= v.Bulge != 0.0;
					}

					if (!identity && !basis.IsUniform && anyBulge)
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
						poly.Normal
					);
					yield break;
				}

				case MText mtext:
				{
					double[] p = Xyz(t, mtext.InsertPoint);
					yield return Primitive.TextAt(
						h,
						flags,
						p[0],
						p[1],
						p[2],
						mtext.Height * basis.ScaleY,
						mtext.Rotation + (identity ? 0.0 : basis.Rotation),
						mtext.Value ?? string.Empty
					);
					yield break;
				}

				// AttributeEntity and AttributeDefinition derive from
				// TextEntity, so this arm carries the text a block instance
				// actually shows.
				case TextEntity text:
				{
					double[] p = Xyz(t, text.InsertPoint);
					yield return Primitive.TextAt(
						h,
						flags,
						p[0],
						p[1],
						p[2],
						text.Height * basis.ScaleY,
						text.Rotation + (identity ? 0.0 : basis.Rotation),
						text.Value ?? string.Empty
					);
					yield break;
				}

				// DIMENSION and HATCH are not here. They are composites: they
				// expand into other entities, and expanding them from inside
				// Map is what put a file-controlled recursion on the CLR
				// stack. Walk dispatches them onto its own stack instead.

				default:
				{
					Primitive w = Primitive.Warning(
						WarningCodes.UnsupportedEntity,
						h,
						e.ObjectName + " is not a primitive this version flattens"
					);
					w.Flags = flags;
					yield return w;
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
			int n = points.Length / 3;

			// The array is left out when every span is straight, which keeps a
			// plain polyline the size it was. This is the one place `== 0.0`
			// on a double belongs in this file: it is a size decision, not a
			// geometry one. A bulge of 1e-300 is emitted exactly as read,
			// because calling it straight is a tolerance decision and the
			// consumer is the only layer that knows its tolerance.
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

			return Primitive.Polyline(
				handle, flags, closed, points, b, normal.X, normal.Y, normal.Z);
		}

		private static Primitive NonUniform(ulong handle, uint flags, string what)
		{
			Primitive w = Primitive.Warning(
				WarningCodes.NonUniformBlockScale,
				handle,
				what + " crossed under a block transform that is not a similarity, so its "
					+ "parameters describe a shape the transform does not preserve"
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
						Transform = item.Transform,
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
			Transform t = item.Transform;
			int loops = 0;

			foreach (Hatch.BoundaryPath path in hatch.Paths)
			{
				if (path.Edges.Count == 0)
				{
					continue;
				}

				loops++;
				double[] pts;
				if (TryPolygon(path, hatch.Elevation, t, out pts))
				{
					CheckPointCount(pts.Length / 3, "a Polygon record");
					yield return new Pending
					{
						Record = Primitive.Polygon(
							h, flags, pts, null, hatch.Normal.X, hatch.Normal.Y, hatch.Normal.Z),
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
						Transform = t,
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
					Transform = t,
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

		// A boundary loop becomes one closed polygon only when every edge is
		// straight, because wire version 1's Polygon is a run of points and a
		// curved edge is not one. A loop with any curve in it returns false
		// and the caller emits the edges as their own records, which keeps
		// every parameter rather than straightening it.
		private static bool TryPolygon(
			Hatch.BoundaryPath path,
			double elevation,
			Transform t,
			out double[] points
		)
		{
			List<double> pts = new List<double>();
			points = null;

			foreach (Hatch.BoundaryPath.Edge edge in path.Edges)
			{
				switch (edge)
				{
					case Hatch.BoundaryPath.Line line:
						Append(pts, t, new XYZ(line.Start.X, line.Start.Y, elevation));
						break;

					case Hatch.BoundaryPath.Polyline poly:
						if (poly.HasBulge)
						{
							return false;
						}

						foreach (XYZ v in poly.Vertices)
						{
							Append(pts, t, new XYZ(v.X, v.Y, elevation));
						}

						break;

					default:
						return false;
				}
			}

			if (pts.Count == 0)
			{
				return false;
			}

			points = pts.ToArray();
			return true;
		}
	}
}
