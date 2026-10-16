using System.Collections.Generic;
using ACadSharp.Entities;
using CSMath;
using Viprs.Wire;

// RAY and XLINE, as record 15 (wire version 3).
//
// Both are a line that does not end: a RAY runs one way from its base point
// and an XLINE runs both ways through it. Every record wire version 2 defines
// is bounded, which is why ADR 0002 refused both for a window, and a bounded
// record is exactly what they cannot honestly be.
//
// The clip is the consumer's, and that is the decision this file exists to
// keep. Clipping to the drawing's extents here and emitting a Line is the
// easiest thing to write and the hardest to notice being wrong, because a
// drawing's stated extents cannot be trusted (g13_bad_extents.dwg is in the
// corpus for exactly that reason) and the consumer is the one that knows its
// viewport. So the record carries a base point and a unit direction, and
// nothing about how far.
//
// The direction is a vector, so it goes through the placement's linear part:
// the difference of two transformed points rather than a transformed point.
// It is normalised here, once, so no consumer has to, and a direction that is
// the zero vector is refused rather than emitted, because there is no line to
// carry. A direction that is not finite is NOT caught here on purpose: it
// falls through to the finiteness guard, which names the handle and says what
// was wrong, rather than being reported as a zero vector it is not.
//
// RAY and XLINE are one record with a kind, 0 and 1, because they are one
// piece of work and a consumer's arm for them is the same arm with one branch.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		private IEnumerable<Primitive> UnboundedRecord(
			string what,
			uint kind,
			XYZ start,
			XYZ direction,
			ulong h,
			uint flags,
			Placement place
		)
		{
			XYZ b = place.Apply(start);
			XYZ d = place.Apply(start + direction) - b;
			double len = d.GetLength();

			if (len < Eps || direction.GetLength() < Eps)
			{
				Primitive w = Primitive.Warning(
					WarningCodes.UnsupportedEntity,
					h,
					what + " has a direction of length zero, so there is no line to carry"
				);
				w.Flags = flags;
				yield return w;
				yield break;
			}

			yield return Primitive.Unbounded(
				h,
				flags,
				kind,
				b.X,
				b.Y,
				b.Z,
				d.X / len,
				d.Y / len,
				d.Z / len
			);
		}
	}
}
