using System.Collections.Generic;
using System.Globalization;
using ACadSharp.Entities;
using CSMath;
using Viprs.Wire;

// LEADER: the older of the two leader entities, and the one whose geometry is
// already geometry.
//
// A LEADER is a run of points and a reference to whatever it points at. The
// run is what this file emits, as one open record 4 through the vertices the
// drawing holds, in the order it holds them. Nothing is synthesised and
// nothing is worked out: the hook line, where the drawing recorded one, is
// already the last vertex of the run, so an arm that appended one would be
// drawing a second copy of it.
//
// Three decisions are worth having written down here, because each one has a
// plausible opposite that produces output a consumer cannot tell from a right
// answer.
//
//  1. The vertices are NOT lifted. Leader is `Entity, IOrientable` and carries
//     a normal at DXF 210, which is the shape the SOLID and TEXT arms treat as
//     "these coordinates are in the plane the normal names". A LEADER's are
//     not: DXF 10 on a LEADER is world, and the DWG reader reads them as plain
//     3BD, so lifting one through the arbitrary axis algorithm moves it out of
//     the drawing. The record still carries the entity's plane, because record
//     4 has a slot for it and a consumer measuring anything in-plane needs it,
//     which is exactly what the `Polyline3D` branch of the IPolyline arm does
//     for the same reason.
//
//  2. A spline-fit leader is refused rather than straightened. Its vertices
//     are fit points: the curve they describe is not in the file as a curve,
//     so a polyline through them is a tessellation by another name, and this
//     layer tessellates nothing (docs/WIRE.md, and the reason an ARC crosses
//     as an ARC). Refusing it is the honest answer and 100 is the code, since
//     a wire version that can carry a fitted spline would change it.
//
//  3. The arrowhead is a warning rather than geometry. It is a glyph the
//     dimension style names, drawn at a size the style sets, and none of that
//     is in this file: inventing a triangle at the first vertex would be this
//     layer deciding what the drawing looks like. So the vertex run crosses
//     and warning 114 beside it says the tip is bare, which is a consumer's
//     decision to make rather than a silence.
//
// What is NOT here is any expansion at all, and that is the point of the file
// being this short. A LEADER's annotation, the MTEXT or TOLERANCE or INSERT it
// labels, is another entity, and it is a root of the document in its own right
// that the walk visits anyway. Reaching for it from here would mean calling
// Map on an entity a file chose, from inside Map, which is the file-controlled
// recursion on the CLR stack that DIMENSION and HATCH were moved onto Walk's
// own stack to get rid of. Nothing in this file hands an entity to anything.
// MULTILEADER is the one in this group that genuinely does expand, and when it
// lands it goes through Walk's stack under CheckDepth the way an INSERT does.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		// One LEADER, as at most two records: the arrowhead warning if the
		// drawing asked for one, then the vertex run.
		private IEnumerable<Primitive> LeaderPolyline(
			Leader leader,
			ulong h,
			uint flags,
			Placement place
		)
		{
			if (leader.PathType == LeaderPathType.Spline)
			{
				yield return LeaderRefused(
					h,
					flags,
					"LEADER is a spline-fit leader and its curve is not in the file as a "
						+ "curve: a polyline through its fit points would be a tessellation "
						+ "by another name, so it is not emitted"
				);
				yield break;
			}

			List<XYZ> vertices = leader.Vertices;
			int n = vertices == null ? 0 : vertices.Count;
			if (n < 2)
			{
				// A one-point leader is a file saying something it cannot mean.
				// Record 4 would take it, which is the reason to refuse it here
				// rather than let it through: a consumer walking spans over a
				// run of one has no span to walk and nothing on the wire told
				// it so.
				yield return LeaderRefused(
					h,
					flags,
					"LEADER carries " + n.ToString(CultureInfo.InvariantCulture)
						+ " vertices and needs two"
				);
				yield break;
			}

			// Before the list exists, which is the rule the Spline and
			// LwPolyline arms follow: the bound is a bound on what gets
			// allocated, and a file controls this count.
			CheckPointCount(n, "a Polyline record");

			if (leader.ArrowHeadEnabled)
			{
				Primitive missing = Primitive.Warning(
					WarningCodes.ArrowheadNotDrawn,
					h,
					"LEADER asks for an arrowhead at its first vertex and this version "
						+ "draws the vertex run only; the arrowhead is a glyph the "
						+ "dimension style names and it is not emitted"
				);
				missing.Flags = flags;
				yield return missing;
			}

			List<double> pts = new List<double>(n * 3);
			for (int i = 0; i < n; i++)
			{
				Append(pts, place, vertices[i]);
			}

			// The plane is named and the points are not lifted through it,
			// which is reason 1 in the header and the only place the two halves
			// of that sentence are both visible.
			XYZ normal = place.WithOcs(leader.Normal, 0.0).Basis.Normal;

			// No bulge array: a leader's spans are straight, and record 4
			// leaves the array out entirely when every span is.
			yield return Primitive.Polyline(
				h,
				flags,
				false,
				pts.ToArray(),
				null,
				normal.X,
				normal.Y,
				normal.Z
			);
		}

		// The two sentences above go out on 100 rather than on 109, and the
		// difference is the one RefusedKinds.cs is about. Neither is "somebody
		// looked at LEADER and decided against it": the kind is implemented,
		// and these are two shapes of LEADER a later version could carry, one
		// on a wire that can hold a fitted curve and one on a file that is not
		// contradicting itself. So they are 100, the message names the kind
		// first, and LEADER stays out of the refusal table.
		private static Primitive LeaderRefused(ulong handle, uint flags, string reason)
		{
			Primitive w = Primitive.Warning(WarningCodes.UnsupportedEntity, handle, reason);
			w.Flags = flags;
			return w;
		}
	}
}
