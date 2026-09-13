// The numeric warning codes this adapter emits.
//
// docs/WIRE.md owns them. It fixes the Warning record's shape, enumerates
// every VIPRS-defined code with its meaning, and splits the number space:
// 1 to 999 is VIPRS, 1000 and up belongs to whichever source read the
// drawing. This file is the C# spelling of the VIPRS half, and a code added
// here without a row in WIRE.md is a number a consumer is told to branch on
// and given no way to learn.
//
// That was the state of it until this was written down: the codes lived here,
// the specification said only that the field was a uint32, and the one thing
// a consumer could do with a warning was count it. Adding a code is still not
// a wire version bump, because a consumer skips a code it does not know
// rather than refusing the stream. That rule is in WIRE.md too, and it is
// what makes the range split safe.
namespace Viprs.Cad
{
	internal static class WarningCodes
	{
		// An entity kind this version does not flatten. The message names the
		// DXF entity type and the record carries its handle, which together
		// are enough to find the thing in the drawing.
		public const uint UnsupportedEntity = 100u;

		// An ACadSharp reader notification, verbatim. None is dropped.
		public const uint ReaderNotification = 101u;

		// A DIMENSION with no block geometry to take lines and text from.
		public const uint DimensionWithoutBlock = 102u;

		// A HATCH with no boundary loop that could become a Polygon.
		public const uint HatchPatternOnly = 103u;

		// A boundary loop carrying an ellipse or spline edge, which a closed
		// polygon cannot express. The edges follow as their own records, so
		// nothing is lost and nothing is approximated.
		public const uint HatchLoopNotPolygon = 104u;

		// An INSERT whose block could not be resolved, which is what an
		// unresolved external reference looks like from inside. Never a
		// fetch, never a read of anything outside the file being decoded.
		public const uint UnresolvedBlock = 105u;

		// An INSERT scale that is not a similarity, under which a circle is
		// an ellipse and a bulge is an elliptical arc. The parameters still
		// cross unchanged; this says they were measured in a frame the
		// transform does not preserve.
		public const uint NonUniformBlockScale = 106u;

		public static string Name(uint code)
		{
			switch (code)
			{
				case UnsupportedEntity: return "UNSUPPORTED_ENTITY";
				case ReaderNotification: return "READER_NOTIFICATION";
				case DimensionWithoutBlock: return "DIMENSION_WITHOUT_BLOCK";
				case HatchPatternOnly: return "HATCH_PATTERN_ONLY";
				case HatchLoopNotPolygon: return "HATCH_LOOP_NOT_POLYGON";
				case UnresolvedBlock: return "UNRESOLVED_BLOCK";
				case NonUniformBlockScale: return "NON_UNIFORM_BLOCK_SCALE";
				default: return "WARNING_" + code;
			}
		}
	}
}
