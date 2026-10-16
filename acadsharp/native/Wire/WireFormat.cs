// The VACB batch protocol's constants, in one place, matching docs/WIRE.md.
//
// Three implementations carry these numbers separately: this one writes the
// stream, and the two conformance consumers parse it. A number that is right
// in two of the three shows up as one record type quietly going missing from
// a large drawing, months later, so a test compares all three as text.
namespace Viprs.Wire
{
	internal static class WireFormat
	{
		// 'V' 'A' 'C' 'B'. Compared byte by byte rather than as one integer,
		// so nothing here depends on the host's endianness.
		public static readonly byte[] Magic = new byte[] { 0x56, 0x41, 0x43, 0x42 };

		public const ushort Version = 2;

		// magic(4) + wire_version(2) + flags(2) + payload_length(4)
		public const int BatchHeaderBytes = 12;

		// type(2) + reserved(2) + length(4)
		public const int RecordHeaderBytes = 8;

		// Record lengths are padded to this, so the record after one always
		// starts where the producer meant it to.
		public const int RecordAlignment = 4;

		// Set on the last batch of a stream. A consumer streaming batches
		// onward can then tell which one is last without tracking the call
		// that produced it.
		public const ushort FlagLast = 1;

		// Fill to the target, stop before the maximum. The one exception is a
		// single record larger than the maximum, which becomes a batch of its
		// own because a batch never splits a record.
		public const int TargetBatchBytes = 65536;
		public const int MaxBatchBytes = 1048576;

		// Types from here up carry no meaning in wire version 1 and a
		// consumer skips them by length. The decoder emits one into every
		// stream so that skip path runs on real bytes rather than only on a
		// buffer a test assembled by hand.
		public const int ForwardProbeFirst = 0x7F00;

		// Prefixed, the way both conformance consumers prefix theirs. It also
		// keeps the three tables mechanically comparable: a test reads the
		// record types out of all three files and a constant that is not one
		// of the thirteen must not look like one.
		public const ushort TypeDocumentBegin = 1;
		public const ushort TypeViewBegin = 2;
		public const ushort TypeLine = 3;
		public const ushort TypePolyline = 4;
		public const ushort TypeArc = 5;
		public const ushort TypeCircle = 6;
		public const ushort TypeEllipse = 7;
		public const ushort TypeSpline = 8;
		public const ushort TypePolygon = 9;
		public const ushort TypeText = 10;
		public const ushort TypeWarning = 11;
		public const ushort TypeViewEnd = 12;
		public const ushort TypeDocumentEnd = 13;

		internal const ushort ForwardProbe = (ushort)ForwardProbeFirst;

		// The geometry records, which are the ones docs/WIRE.md's finiteness
		// guarantee covers.
		//
		// A set, listed one type at a time, and not a range. The numbering is
		// frozen, but "frozen" does not make the geometry records contiguous:
		// 11, 12 and 13 are Warning, ViewEnd and DocumentEnd, so the first
		// geometry record wire 3 adds is numbered 14 and a range from Line to
		// Text would have left it outside. Flattener.Finite() returns early for
		// anything this refuses, so a NaN in such a record would have crossed
		// the wire while the guarantee stayed literally true (ADR 0002 and
		// ADR 0003 both describe the trap).
		//
		// So a new geometry record is added here, in the same change that
		// gives it a constant, and tests/test_isgeometry_set.py fails when a
		// constant is in neither this set nor its list of types that are not
		// geometry.
		//
		// ViewBegin is outside it on purpose: its extents are a bounding box
		// the source reports rather than a shape somebody draws, and a view
		// holding nothing reports one that is not finite. Promising a finite
		// extent would mean inventing a number for the empty case, which is
		// worse than saying nothing.
		public static bool IsGeometry(ushort type)
		{
			switch (type)
			{
				case TypeLine:
				case TypePolyline:
				case TypeArc:
				case TypeCircle:
				case TypeEllipse:
				case TypeSpline:
				case TypePolygon:
				case TypeText:
					return true;
				default:
					return false;
			}
		}

		public static int PadTo4(int n)
		{
			return (4 - (n % 4)) % 4;
		}
	}
}
