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

			public bool IsSimilarity
			{
				get
				{
					double m = Math.Max(Math.Max(ScaleX, ScaleY), 1.0);
					return !Mirrored && Math.Abs(ScaleX - ScaleY) <= 1e-9 * m;
				}
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
					if (!identity && !basis.IsSimilarity && HasBulge(lw))
					{
						yield return NonUniform(h, flags, "LWPOLYLINE");
					}

					CheckPointCount(lw.Vertices.Count, "a Polyline record");
					List<double> pts = new List<double>(lw.Vertices.Count * 3);
					double[] lwBulges = new double[lw.Vertices.Count];
					for (int i = 0; i < lw.Vertices.Count; i++)
					{
						LwPolyline.Vertex v = lw.Vertices[i];
						Append(pts, t, new XYZ(v.Location.X, v.Location.Y, lw.Elevation));
						lwBulges[i] = v.Bulge;
					}

					foreach (Primitive p in EmitPolyline(
						h, flags, lw.IsClosed, pts.ToArray(), lwBulges, lw.Normal))
					{
						yield return p;
					}

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

					if (!identity && !basis.IsSimilarity && anyBulge)
					{
						yield return NonUniform(h, flags, "POLYLINE");
					}

					foreach (Primitive p in EmitPolyline(
						h, flags, poly.IsClosed, pts.ToArray(), bulges.ToArray(), poly.Normal))
					{
						yield return p;
					}

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

		// A polyline, as wire version 1 can carry it.
		//
		// docs/WIRE.md's Polyline is a point count and a run of triples: it
		// has nowhere to put a bulge. Dropping the bulge would turn an arc
		// into a chord, which is the tessellation this whole lane exists to
		// avoid, and only worse because a chord is not even a good
		// approximation of an arc. Adding a field would be a wire version
		// bump, which belongs to the ABI issue and not to this one.
		//
		// So a bulged polyline goes out as what it is: the straight runs as
		// Polyline records and every bulged span as an Arc carrying its own
		// centre, radius and angles. Nothing is approximated, every record
		// keeps the source entity's handle so a consumer can regroup them,
		// and a polyline with no bulge anywhere is still exactly one record.
		private static IEnumerable<Primitive> EmitPolyline(
			ulong handle,
			uint flags,
			bool closed,
			double[] points,
			double[] bulges,
			XYZ normal
		)
		{
			int n = points.Length / 3;
			bool anyBulge = false;
			for (int i = 0; i < bulges.Length && i < n; i++)
			{
				if (bulges[i] != 0.0 && (closed || i + 1 < n))
				{
					anyBulge = true;
					break;
				}
			}

			if (!anyBulge || n < 2)
			{
				yield return Primitive.Polyline(
					handle, flags, closed, points, null, normal.X, normal.Y, normal.Z);
				yield break;
			}

			List<double> run = new List<double>();
			int segments = closed ? n : n - 1;
			for (int i = 0; i < segments; i++)
			{
				int j = (i + 1) % n;
				double b = i < bulges.Length ? bulges[i] : 0.0;

				if (b == 0.0)
				{
					if (run.Count == 0)
					{
						AddPoint(run, points, i);
					}

					AddPoint(run, points, j);
					continue;
				}

				foreach (Primitive p in Flush(handle, flags, run, normal))
				{
					yield return p;
				}

				yield return ArcFromBulge(handle, flags, points, i, j, b, normal);
			}

			foreach (Primitive p in Flush(handle, flags, run, normal))
			{
				yield return p;
			}
		}

		private static void AddPoint(List<double> into, double[] points, int index)
		{
			into.Add(points[(index * 3) + 0]);
			into.Add(points[(index * 3) + 1]);
			into.Add(points[(index * 3) + 2]);
		}

		private static IEnumerable<Primitive> Flush(
			ulong handle, uint flags, List<double> run, XYZ normal)
		{
			if (run.Count >= 6)
			{
				yield return Primitive.Polyline(
					handle, flags, false, run.ToArray(), null, normal.X, normal.Y, normal.Z);
			}

			run.Clear();
		}

		// The arc a bulge names, exactly.
		//
		// A bulge is the tangent of a quarter of the included angle, negative
		// when the arc runs clockwise from the first point to the second, so
		// the centre and radius follow in closed form from the two endpoints
		// and that one number. The record's angles are counter-clockwise, as
		// docs/WIRE.md requires, which is why a negative bulge swaps them
		// rather than being carried as a sign.
		private static Primitive ArcFromBulge(
			ulong handle,
			uint flags,
			double[] points,
			int i,
			int j,
			double bulge,
			XYZ normal
		)
		{
			double x0 = points[(i * 3) + 0];
			double y0 = points[(i * 3) + 1];
			double z0 = points[(i * 3) + 2];
			double x1 = points[(j * 3) + 0];
			double y1 = points[(j * 3) + 1];
			double z1 = points[(j * 3) + 2];

			double k = (1.0 - (bulge * bulge)) / (4.0 * bulge);
			double cx = ((x0 + x1) / 2.0) - ((y1 - y0) * k);
			double cy = ((y0 + y1) / 2.0) + ((x1 - x0) * k);
			double cz = (z0 + z1) / 2.0;

			double dx = x1 - x0;
			double dy = y1 - y0;
			double chord = Math.Sqrt((dx * dx) + (dy * dy));
			double radius = chord * (1.0 + (bulge * bulge)) / (4.0 * Math.Abs(bulge));

			double a0 = Math.Atan2(y0 - cy, x0 - cx);
			double a1 = Math.Atan2(y1 - cy, x1 - cx);
			if (bulge < 0.0)
			{
				double swap = a0;
				a0 = a1;
				a1 = swap;
			}

			return Primitive.Arc(
				handle,
				flags,
				cx,
				cy,
				cz,
				radius,
				a0,
				a1,
				normal.X,
				normal.Y,
				normal.Z
			);
		}

		private static bool HasBulge(LwPolyline lw)
		{
			foreach (LwPolyline.Vertex v in lw.Vertices)
			{
				if (v.Bulge != 0.0)
				{
					return true;
				}
			}

			return false;
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
