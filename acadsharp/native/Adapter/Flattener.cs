using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using ACadSharp.Blocks;
using ACadSharp.Entities;
using ACadSharp.Tables;
using CSMath;

namespace Viprs.Cad;

// The walk that turns a resident document into the flattened record stream,
// and the only place any of the ABI's limits are applied.
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
public sealed class Flattener
{
	private const double Eps = 1e-12;

	private readonly AdapterLimits _limits;
	private ulong _entities;
	private ulong _outputBytes;

	public Flattener(AdapterLimits limits)
	{
		this._limits = limits ?? AdapterLimits.Defaults();
	}

	public ulong EntitiesVisited
	{
		get { return this._entities; }
	}

	public ulong OutputBytes
	{
		get { return this._outputBytes; }
	}

	private struct Pending
	{
		public Entity Entity;
		public Transform Transform;
		public int Depth;
	}

	private struct Frame
	{
		public IEnumerator<Pending> Items;
	}

	// -------------------------------------------------------------- limits

	private void CountEntity()
	{
		this._entities++;
		if (this._entities > this._limits.MaxEntities)
		{
			throw new AdapterLimitException(
				"max_entities",
				"the decode reached entity " + this._entities.ToString(CultureInfo.InvariantCulture)
				+ " and max_entities is " + this._limits.MaxEntities.ToString(CultureInfo.InvariantCulture)
				+ ", counted across the whole decode with block expansion included");
		}
	}

	private void CheckString(string s, string what)
	{
		if (s is null)
		{
			return;
		}

		int n = Encoding.UTF8.GetByteCount(s);
		if ((ulong)n > this._limits.MaxStringBytes)
		{
			throw new AdapterLimitException(
				"max_string_bytes",
				what + " is " + n.ToString(CultureInfo.InvariantCulture)
				+ " UTF-8 bytes and max_string_bytes is "
				+ this._limits.MaxStringBytes.ToString(CultureInfo.InvariantCulture));
		}
	}

	private void CheckPoints(Record r)
	{
		int n = r.Points is null ? 0 : r.Points.Length;
		if ((ulong)n > this._limits.MaxPolylinePoints)
		{
			throw new AdapterLimitException(
				"max_polyline_points",
				"a " + r.Kind + " record carries " + n.ToString(CultureInfo.InvariantCulture)
				+ " vertices and max_polyline_points is "
				+ this._limits.MaxPolylinePoints.ToString(CultureInfo.InvariantCulture));
		}
	}

	// Every record leaves through here, so every bound that is about a record
	// rather than about an entity is applied exactly once.
	private Record Admit(Record r)
	{
		if (r.Kind == RecordKind.Polyline || r.Kind == RecordKind.Polygon || r.Kind == RecordKind.Spline)
		{
			this.CheckPoints(r);
		}

		if (r.Kind == RecordKind.Text)
		{
			this.CheckString(r.Text, "a Text record's value");
		}

		if (r.Kind == RecordKind.Warning)
		{
			this.CheckString(r.Text, "a Warning record's message");
		}

		this._outputBytes += (ulong)RecordEncoder.Size(r);
		if (this._outputBytes > this._limits.MaxOutputBytes)
		{
			throw new AdapterLimitException(
				"max_output_bytes",
				"the decode has emitted " + this._outputBytes.ToString(CultureInfo.InvariantCulture)
				+ " bytes and max_output_bytes is "
				+ this._limits.MaxOutputBytes.ToString(CultureInfo.InvariantCulture));
		}

		return r;
	}

	// ------------------------------------------------------------ geometry

	private static Vec3 P(Transform t, XYZ p)
	{
		XYZ v = t.ApplyTransform(p);
		return new Vec3(v.X, v.Y, v.Z);
	}

	private static Vec3 V(XYZ p)
	{
		return new Vec3(p.X, p.Y, p.Z);
	}

	// The in-plane scale factors and rotation a transform applies, measured
	// rather than decomposed, because a composed chain of inserts is not
	// guaranteed to decompose into the translate/rotate/scale it was built
	// from and a measurement of the basis always is.
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
				double m = Math.Max(Math.Max(this.ScaleX, this.ScaleY), 1.0);
				return !this.Mirrored && Math.Abs(this.ScaleX - this.ScaleY) <= 1e-9 * m;
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

	// ------------------------------------------------------------ the walk

	public IEnumerable<Record> Walk(IEnumerable<Entity> roots)
	{
		Stack<Frame> stack = new Stack<Frame>();
		stack.Push(new Frame { Items = Root(roots).GetEnumerator() });

		try
		{
			while (stack.Count > 0)
			{
				Frame frame = stack.Peek();
				if (!frame.Items.MoveNext())
				{
					stack.Pop();
					frame.Items.Dispose();
					continue;
				}

				Pending item = frame.Items.Current;
				this.CountEntity();

				if (item.Entity is Insert insert)
				{
					IEnumerator<Pending> expanded = this.ExpandInsert(insert, item, out Record refusal);
					if (refusal != null)
					{
						yield return this.Admit(refusal);
						continue;
					}

					stack.Push(new Frame { Items = expanded });
					continue;
				}

				foreach (Record r in this.Map(item.Entity, item.Transform))
				{
					yield return this.Admit(r);
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

	// --------------------------------------------------------------- INSERT

	private IEnumerator<Pending> ExpandInsert(Insert insert, Pending item, out Record refusal)
	{
		refusal = null;

		BlockRecord block = insert.Block;
		if (block is null)
		{
			refusal = Warning(
				WarningCode.UnresolvedBlock,
				insert.Handle,
				LayerOf(insert),
				"INSERT names no block record");
			return Empty();
		}

		Block header = block.BlockEntity;
		bool isXref = header != null
			&& (header.Flags.HasFlag(BlockTypeFlags.XRef) || header.Flags.HasFlag(BlockTypeFlags.XRefOverlay));
		if (isXref || (header != null && header.IsUnloaded))
		{
			// An external reference is another file. This shim never opens
			// one: no network, no second read, no subprocess. The reference
			// crosses as a warning naming the path the drawing recorded, and
			// whoever wants it resolved resolves it themselves.
			string path = header is null || string.IsNullOrEmpty(header.XRefPath)
				? block.Name
				: header.XRefPath;
			refusal = Warning(
				WarningCode.UnresolvedBlock,
				insert.Handle,
				LayerOf(insert),
				"external reference " + path + " is not resolved, and this decoder never resolves one");
			return Empty();
		}

		if ((uint)(item.Depth + 1) > this._limits.MaxBlockDepth)
		{
			throw new AdapterLimitException(
				"max_block_depth",
				"INSERT " + insert.Handle.ToString("X", CultureInfo.InvariantCulture)
				+ " of block " + block.Name + " is at nesting depth "
				+ (item.Depth + 1).ToString(CultureInfo.InvariantCulture)
				+ " and max_block_depth is "
				+ this._limits.MaxBlockDepth.ToString(CultureInfo.InvariantCulture));
		}

		return this.InsertBody(insert, block, item).GetEnumerator();
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

		// The attributes belong to the INSERT and are already placed in the
		// frame the INSERT sits in, so they are emitted under the parent
		// transform rather than under the block's.
		foreach (AttributeEntity att in insert.Attributes)
		{
			yield return new Pending { Entity = att, Transform = item.Transform, Depth = item.Depth };
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
						new XYZ((dx * cosr) - (dy * sinr), (dx * sinr) + (dy * cosr), 0.0));
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

	// ------------------------------------------------------------- mapping

	private static string LayerOf(Entity e)
	{
		Layer l = e is null ? null : e.Layer;
		return l is null || l.Name is null ? string.Empty : l.Name;
	}

	private static Record Warning(string code, ulong handle, string layer, string message)
	{
		return new Record
		{
			Kind = RecordKind.Warning,
			Handle = handle,
			Layer = layer ?? string.Empty,
			Code = code,
			Text = message ?? string.Empty,
		};
	}

	public static Record ReaderNotification(string message)
	{
		return new Record
		{
			Kind = RecordKind.Warning,
			Handle = 0UL,
			Layer = string.Empty,
			Code = WarningCode.ReaderNotification,
			Text = message ?? string.Empty,
		};
	}

	private IEnumerable<Record> Map(Entity e, Transform t, ulong handleOverride = 0UL)
	{
		ulong h = handleOverride != 0UL ? handleOverride : e.Handle;
		string layer = LayerOf(e);
		Basis basis = MeasureBasis(t);
		bool identity = IsIdentityish(t);

		switch (e)
		{
			case Line line:
				yield return new Record
				{
					Kind = RecordKind.Line,
					Handle = h,
					Layer = layer,
					A = P(t, line.StartPoint),
					B = P(t, line.EndPoint),
					N = P(t, line.Normal),
				};
				yield break;

			// Arc before Circle: ACadSharp's Arc derives from Circle, so the
			// other order silently turns every arc into a full circle.
			case Arc arc:
			{
				if (!identity && !basis.IsSimilarity)
				{
					yield return NonUniform(h, layer, "ARC");
				}
				yield return new Record
				{
					Kind = RecordKind.Arc,
					Handle = h,
					Layer = layer,
					A = P(t, arc.Center),
					R = arc.Radius * basis.ScaleX,
					A0 = arc.StartAngle + (identity ? 0.0 : basis.Rotation),
					A1 = arc.EndAngle + (identity ? 0.0 : basis.Rotation),
					N = V(arc.Normal),
				};
				yield break;
			}

			case Circle circle:
			{
				if (!identity && !basis.IsSimilarity)
				{
					yield return NonUniform(h, layer, "CIRCLE");
				}
				yield return new Record
				{
					Kind = RecordKind.Circle,
					Handle = h,
					Layer = layer,
					A = P(t, circle.Center),
					R = circle.Radius * basis.ScaleX,
					N = V(circle.Normal),
				};
				yield break;
			}

			case Ellipse ellipse:
			{
				if (!identity && !basis.IsSimilarity)
				{
					yield return NonUniform(h, layer, "ELLIPSE");
				}
				XYZ centre = ellipse.Center;
				XYZ major = ellipse.MajorAxisEndPoint;
				XYZ tc = t.ApplyTransform(centre);
				XYZ tm = t.ApplyTransform(centre + major) - tc;
				yield return new Record
				{
					Kind = RecordKind.Ellipse,
					Handle = h,
					Layer = layer,
					A = new Vec3(tc.X, tc.Y, tc.Z),
					B = new Vec3(tm.X, tm.Y, tm.Z),
					R = ellipse.RadiusRatio,
					A0 = ellipse.StartParameter,
					A1 = ellipse.EndParameter,
					N = V(ellipse.Normal),
				};
				yield break;
			}

			case Spline spline:
			{
				// A spline's control points are affine, so any transform this
				// walk can apply lands exactly on the transformed control
				// points. Knots and weights are untouched by construction.
				List<XYZ> ctrl = spline.ControlPoints;
				Vec3[] pts = new Vec3[ctrl.Count];
				for (int i = 0; i < ctrl.Count; i++)
				{
					pts[i] = P(t, ctrl[i]);
				}

				yield return new Record
				{
					Kind = RecordKind.Spline,
					Handle = h,
					Layer = layer,
					Degree = spline.Degree,
					Points = pts,
					Knots = spline.Knots.ToArray(),
					Weights = spline.Weights.ToArray(),
					Closed = (byte)(spline.Flags.HasFlag(SplineFlags.Closed) ? 1 : 0),
					N = V(spline.Normal),
				};
				yield break;
			}

			case LwPolyline lw:
			{
				if (!identity && !basis.IsSimilarity && HasBulge(lw))
				{
					yield return NonUniform(h, layer, "LWPOLYLINE");
				}

				int n = lw.Vertices.Count;
				Vec3[] pts = new Vec3[n];
				double[] bulges = new double[n];
				for (int i = 0; i < n; i++)
				{
					LwPolyline.Vertex v = lw.Vertices[i];
					pts[i] = P(t, new XYZ(v.Location.X, v.Location.Y, lw.Elevation));
					bulges[i] = v.Bulge;
				}

				yield return new Record
				{
					Kind = RecordKind.Polyline,
					Handle = h,
					Layer = layer,
					Points = pts,
					Bulges = bulges,
					Closed = (byte)(lw.IsClosed ? 1 : 0),
					N = V(lw.Normal),
				};
				yield break;
			}

			case IPolyline poly:
			{
				List<Vec3> pts = new List<Vec3>();
				List<double> bulges = new List<double>();
				bool anyBulge = false;
				foreach (IVertex v in poly.Vertices)
				{
					IVector loc = v.Location;
					double x = loc.Dimension > 0 ? loc[0] : 0.0;
					double y = loc.Dimension > 1 ? loc[1] : 0.0;
					double z = loc.Dimension > 2 ? loc[2] : poly.Elevation;
					pts.Add(P(t, new XYZ(x, y, z)));
					bulges.Add(v.Bulge);
					anyBulge |= v.Bulge != 0.0;
				}

				if (!identity && !basis.IsSimilarity && anyBulge)
				{
					yield return NonUniform(h, layer, "POLYLINE");
				}

				yield return new Record
				{
					Kind = RecordKind.Polyline,
					Handle = h,
					Layer = layer,
					Points = pts.ToArray(),
					Bulges = bulges.ToArray(),
					Closed = (byte)(poly.IsClosed ? 1 : 0),
					N = V(poly.Normal),
				};
				yield break;
			}

			case MText mtext:
				yield return new Record
				{
					Kind = RecordKind.Text,
					Handle = h,
					Layer = layer,
					A = P(t, mtext.InsertPoint),
					R = mtext.Height * basis.ScaleY,
					A0 = mtext.Rotation + (identity ? 0.0 : basis.Rotation),
					Text = mtext.Value ?? string.Empty,
					N = V(mtext.Normal),
				};
				yield break;

			// AttributeEntity and AttributeDefinition derive from TextEntity,
			// so this arm carries the text a block instance actually shows.
			case TextEntity text:
				yield return new Record
				{
					Kind = RecordKind.Text,
					Handle = h,
					Layer = layer,
					A = P(t, text.InsertPoint),
					R = text.Height * basis.ScaleY,
					A0 = text.Rotation + (identity ? 0.0 : basis.Rotation),
					Text = text.Value ?? string.Empty,
					N = V(text.Normal),
				};
				yield break;

			case Dimension dim:
			{
				foreach (Record r in this.MapDimension(dim, t))
				{
					yield return r;
				}
				yield break;
			}

			case Hatch hatch:
			{
				foreach (Record r in this.MapHatch(hatch, t))
				{
					yield return r;
				}
				yield break;
			}

			default:
				yield return Warning(
					WarningCode.UnsupportedEntity,
					h,
					layer,
					e.ObjectName + " is not a primitive this version flattens");
				yield break;
		}
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

	private static Record NonUniform(ulong handle, string layer, string what)
	{
		return Warning(
			WarningCode.NonUniformBlockScale,
			handle,
			layer,
			what + " crossed under a block transform that is not a similarity, so its "
			+ "parameters describe a shape the transform does not preserve");
	}

	// ------------------------------------------------------------ DIMENSION

	private IEnumerable<Record> MapDimension(Dimension dim, Transform t)
	{
		BlockRecord block = dim.Block;
		int emitted = 0;
		if (block != null)
		{
			foreach (Entity e in block.Entities)
			{
				// One level only, and never through an INSERT: a dimension
				// block is generated geometry, not a user block, and walking
				// it as a block would give it a second depth budget.
				if (e is Insert)
				{
					continue;
				}

				foreach (Record r in this.Map(e, t))
				{
					emitted++;
					yield return r;
				}
			}
		}

		if (emitted == 0)
		{
			yield return Warning(
				WarningCode.DimensionWithoutBlock,
				dim.Handle,
				LayerOf(dim),
				dim.ObjectName + " carries no block geometry, so there is nothing to draw");
		}
	}

	// ---------------------------------------------------------------- HATCH

	private IEnumerable<Record> MapHatch(Hatch hatch, Transform t)
	{
		ulong h = hatch.Handle;
		string layer = LayerOf(hatch);
		int loops = 0;

		foreach (Hatch.BoundaryPath path in hatch.Paths)
		{
			if (path.Edges.Count == 0)
			{
				continue;
			}

			loops++;
			if (TryPolygon(path, hatch.Elevation, t, out Vec3[] pts, out double[] bulges))
			{
				yield return new Record
				{
					Kind = RecordKind.Polygon,
					Handle = h,
					Layer = layer,
					Points = pts,
					Bulges = bulges,
					Closed = 1,
					N = V(hatch.Normal),
				};
				continue;
			}

			// An ellipse or spline edge cannot be a bulge, and turning it
			// into one would be tessellation by another name. The loop goes
			// out as its own edges instead, which is strictly more than a
			// polygon carries.
			yield return Warning(
				WarningCode.HatchLoopNotPolygon,
				h,
				layer,
				"boundary loop " + loops.ToString(CultureInfo.InvariantCulture)
				+ " carries a curve a closed polygon cannot express, so its edges follow as records");

			foreach (Hatch.BoundaryPath.Edge edge in path.Edges)
			{
				// CadObject.Handle has an internal setter, so a synthesised
				// edge cannot be relabelled. The handle travels beside it.
				Entity entity = edge.ToEntity();
				foreach (Record r in this.Map(entity, t, h))
				{
					yield return r;
				}
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
		// pattern lines should reach the Line arm, not silently change shape.
		int lines = 0;
		foreach (Entity e in hatch.ExplodePattern())
		{
			if (e is Line)
			{
				lines++;
				foreach (Record r in this.Map(e, t))
				{
					yield return r;
				}
			}
		}

		if (lines == 0)
		{
			yield return Warning(
				WarningCode.HatchPatternOnly,
				h,
				layer,
				"HATCH has no boundary loop, and its pattern produced no geometry to draw");
		}
	}

	// A boundary loop becomes one closed polygon when every edge is straight
	// or a circular arc, because an arc is exactly a bulge. Anything else
	// returns false and the caller emits the edges.
	private static bool TryPolygon(
		Hatch.BoundaryPath path,
		double elevation,
		Transform t,
		out Vec3[] points,
		out double[] bulges)
	{
		List<Vec3> pts = new List<Vec3>();
		List<double> bl = new List<double>();
		points = null;
		bulges = null;

		foreach (Hatch.BoundaryPath.Edge edge in path.Edges)
		{
			switch (edge)
			{
				case Hatch.BoundaryPath.Line line:
					pts.Add(P(t, new XYZ(line.Start.X, line.Start.Y, elevation)));
					bl.Add(0.0);
					break;

				case Hatch.BoundaryPath.Arc arc:
				{
					double sweep = arc.EndAngle - arc.StartAngle;
					if (!arc.CounterClockWise)
					{
						sweep = -sweep;
					}

					while (sweep <= 0.0)
					{
						sweep += 2.0 * Math.PI;
					}
					while (sweep > 2.0 * Math.PI)
					{
						sweep -= 2.0 * Math.PI;
					}

					double a0 = arc.CounterClockWise ? arc.StartAngle : arc.EndAngle;
					double bulge = Math.Tan(sweep / 4.0);
					if (!arc.CounterClockWise)
					{
						bulge = -bulge;
					}

					pts.Add(P(t, new XYZ(
						arc.Center.X + (arc.Radius * Math.Cos(a0)),
						arc.Center.Y + (arc.Radius * Math.Sin(a0)),
						elevation)));
					bl.Add(bulge);
					break;
				}

				case Hatch.BoundaryPath.Polyline poly:
				{
					foreach (XYZ v in poly.Vertices)
					{
						pts.Add(P(t, new XYZ(v.X, v.Y, elevation)));
						bl.Add(v.Z);
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
		bulges = bl.ToArray();
		return true;
	}
}
