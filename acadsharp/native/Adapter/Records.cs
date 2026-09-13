namespace Viprs.Cad;

// The record kinds the flattened stream carries. Numbered, not ordered by
// name, because the number is what crosses the boundary.
//
// Arc, Circle, Ellipse and Spline are here as curves carrying their own
// parameters and that is the whole point of the list. libviprs owns zoom and
// precision aware tessellation and is the only layer that knows either, so a
// shim that turned any of these into a Polyline would have thrown away the
// information its consumer needs. tests/test_flatten_curves.py asserts the
// counts that make that a red test rather than a paragraph.
public enum RecordKind : uint
{
	Line = 1u,
	Polyline = 2u,
	Arc = 3u,
	Circle = 4u,
	Ellipse = 5u,
	Spline = 6u,
	Text = 7u,
	Polygon = 8u,
	Warning = 9u,
}

// A VIPRS point. Deliberately not CSMath's XYZ: no upstream type crosses this
// boundary, and the record model is the boundary.
public struct Vec3
{
	public double X;
	public double Y;
	public double Z;

	public Vec3(double x, double y, double z)
	{
		this.X = x;
		this.Y = y;
		this.Z = z;
	}
}

// The warning codes the flattener can raise. Strings rather than an enum
// because they travel as text in the record's payload and a consumer that
// logs one should be able to read it.
public static class WarningCode
{
	// An entity kind this version does not flatten. Carries the type name.
	public const string UnsupportedEntity = "UNSUPPORTED_ENTITY";
	// An ACadSharp reader notification, verbatim. None is dropped.
	public const string ReaderNotification = "READER_NOTIFICATION";
	// A DIMENSION with no block to take lines and text from.
	public const string DimensionWithoutBlock = "DIMENSION_WITHOUT_BLOCK";
	// A HATCH with no boundary loop that could become a Polygon.
	public const string HatchPatternOnly = "HATCH_PATTERN_ONLY";
	// A boundary loop carrying an ellipse or spline edge, which a closed
	// polygon with bulges cannot express. The edges follow as their own
	// records, so nothing is lost, but the loop is no longer one record.
	public const string HatchLoopNotPolygon = "HATCH_LOOP_NOT_POLYGON";
	// An INSERT whose block could not be resolved, which is what an
	// unresolved XREF looks like from inside. Never a fetch, never a read of
	// anything outside the file being decoded.
	public const string UnresolvedBlock = "UNRESOLVED_BLOCK";
	// An INSERT scale that is not a similarity, under which a circle is an
	// ellipse and a bulge is an elliptical arc. The parameters still cross
	// unchanged; this says they were measured in a frame the transform does
	// not preserve.
	public const string NonUniformBlockScale = "NON_UNIFORM_BLOCK_SCALE";
	// Block expansion stopped at max_block_depth. Only ever emitted when the
	// caller asked for truncation instead of a refusal.
	public const string BlockDepthTruncated = "BLOCK_DEPTH_TRUNCATED";
}

// One flattened primitive.
//
// A single class with a kind tag rather than a hierarchy: every record is
// written by the same encoder and dumped by the same formatter, and a visitor
// over nine tiny subclasses would be more code for the same switch.
public sealed class Record
{
	public RecordKind Kind;
	// The handle of the entity this came from. For a primitive lifted out of
	// a block it is the handle of the entity inside the block, so a consumer
	// can still name the thing it is drawing.
	public ulong Handle;
	public string Layer = string.Empty;

	// Line: A start, B end.
	// Arc/Circle: A centre, N normal.
	// Ellipse: A centre, B major axis endpoint relative to the centre.
	// Text: A insert point.
	public Vec3 A;
	public Vec3 B;
	public Vec3 N = new Vec3(0, 0, 1);

	// Arc/Circle radius, Ellipse minor/major ratio.
	public double R;
	// Arc start/end angle in radians, Ellipse start/end parameter,
	// Text rotation in A0 and height in R.
	public double A0;
	public double A1;

	// Polyline/Polygon vertices, and the bulge at each one. A bulge is the
	// tangent of a quarter of the arc's included angle, so a polyline bulge
	// crosses as an arc and is never expanded into segments.
	public Vec3[] Points;
	public double[] Bulges;
	public byte Closed;

	// Spline.
	public int Degree;
	public double[] Knots;
	public double[] Weights;

	// Text value, or the warning message.
	public string Text = string.Empty;
	// Warning only.
	public string Code = string.Empty;
}
