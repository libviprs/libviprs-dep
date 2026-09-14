using System.Collections.Generic;
using System.Globalization;
using ACadSharp.Entities;
using CSMath;
using Viprs.Wire;

// The two entity kinds that are a filled face given by the corners the file
// already holds: SOLID, which is one quadrilateral, and MESH, which is an
// explicit vertex list plus a face list that indexes it. Both lower to
// docs/WIRE.md's record 9, Polygon, which is why they are one file: neither
// needs a new record, neither tessellates anything, and both are entirely a
// question of which corners come out in which order.
//
// What is NOT here is 3DFACE, and the reason is worth having written down
// where the next person looking for it will be.
//
// 3DFACE looks like the same work. ACadSharp gives Face3D the same four
// properties with the same names at the same DXF group codes, 10, 11, 12 and
// 13, so one shared QuadPoints(a, b, c, d) helper is the obvious move. It is
// the wrong one twice over:
//
//  1. The orders differ. DXF stores a SOLID's third and fourth corners
//     swapped relative to traversal order, so a SOLID emits 1, 2, 4, 3. A
//     Face3D additionally carries InvisibleEdgeFlags, whose members are
//     First = 1, Second = 2, Third = 4 and Fourth = 8, and per-edge
//     visibility only means anything if edge 1 is corners 1 to 2, edge 2 is
//     2 to 3, edge 3 is 3 to 4 and edge 4 is 4 to 1. That is traversal
//     order, so a 3DFACE emits 1, 2, 3, 4. A shared helper fixes SOLID and
//     introduces the same bow-tie into 3DFACE.
//  2. The coordinate spaces differ. Solid is `Entity, IOrientable` and
//     carries a Normal at DXF 210, so its corners are object coordinates and
//     have to be lifted. Face3D is a plain Entity, carries no normal at all,
//     and its own property documentation says every corner is in WCS, so
//     lifting one would move it. Record 9 still needs a normal, and taking
//     the placement's would ship +Z for a face standing on its side, so a
//     3DFACE's normal has to be measured off its own corners.
//
// So 3DFACE stays refused for now. What it needs is an isolated fixture: one
// instance on a real drawing cannot tell a corner order from a corner order,
// and every assertion that could is an assertion about an asymmetric quad
// nobody has written yet. ACadSharp's DwgObjectWriter does have a case for
// Face3D with a real writeFace3D method, so that fixture is producible.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		// SOLID as one closed Polygon.
		//
		// The corner order is the whole of this method and it is the one thing
		// about SOLID that is easy to get wrong in a way that looks right.
		//
		// The corners come off the entity in DXF group-code order: FirstCorner
		// at 10, SecondCorner at 11, ThirdCorner at 12, FourthCorner at 13.
		// ACadSharp exposes them verbatim and reorders nothing, so the swap is
		// DXF's own and it is passed through to here. Walking a quadrilateral
		// in that order crosses the middle: the polygon is a bow-tie and not
		// the filled quad the drawing shows.
		//
		// g13_solid.dwg's first solid is the measurement. Its corners are
		// (0,0), (10,1), (2,5) and (11,7), the order below traces a simple
		// quad of area 50, and 1, 2, 3, 4 traces a bow-tie of signed area 3.5.
		// A rectangle cannot tell them apart, which is why that fixture's
		// first solid has no symmetry at all and why an assertion on area or
		// on a bounding box would pass either way.
		private Primitive SolidPolygon(Solid solid, ulong h, uint flags, Placement place)
		{
			// "If only three corners are entered to define the SOLID, then the
			// fourth corner coordinate is the same as the third", which is
			// upstream's own wording and the format's. It is a copy of the
			// stored value rather than a near-miss, so the comparison is exact
			// and is made on what the file holds, before any transform.
			bool triangle = solid.FourthCorner == solid.ThirdCorner;
			int n = triangle ? 3 : 4;
			CheckPointCount(n, "a Polygon record");

			// Object coordinates, exactly as the Arc and Circle arms treat
			// theirs. Solid is IOrientable and carries its normal at DXF 210,
			// and an unlifted corner is not in the drawing at all.
			Placement ocs = place.WithOcs(solid.Normal, 0.0);
			List<double> pts = new List<double>(n * 3);

			// 1, 2, 4, 3. Group codes 10, 11, 13, 12.
			Append(pts, ocs, solid.FirstCorner);
			Append(pts, ocs, solid.SecondCorner);
			if (!triangle)
			{
				Append(pts, ocs, solid.FourthCorner);
			}

			Append(pts, ocs, solid.ThirdCorner);

			Basis basis = ocs.Basis;

			// No bulge array. A solid's edges are straight, and record 9 leaves
			// the array out entirely when every span is, so a filled quad is
			// four vertices and nothing else. That is also why there is no
			// NON_UNIFORM_BLOCK_SCALE warning on this path: a polygon's
			// vertices transform exactly whatever the scale, and the warning
			// exists for parameters that stop naming the shape, which is
			// bulges, radii and angles.
			return Primitive.Polygon(
				h,
				flags,
				pts.ToArray(),
				null,
				basis.Normal.X,
				basis.Normal.Y,
				basis.Normal.Z
			);
		}

		// MESH as one closed Polygon per face of the base mesh.
		//
		// A MESH is the tractable member of #88's group: 3DSOLID and REGION
		// store an embedded ACIS boundary representation that neither
		// ACadSharp nor anything else in this build evaluates, and a MESH
		// stores its vertices and its faces as plain numbers. The faces are in
		// the file, so reading them is a read rather than an evaluation.
		//
		// The subdivision level is the one decision here, and it is decided
		// against evaluating it. Subdividing is a smoothing algorithm: it
		// invents vertices that are not in the drawing, the result depends on
		// the scheme, and "these are the faces the file stores" is a promise
		// this layer can keep while "this is what AutoCAD displays" is not.
		// The base mesh goes out and a warning says the level was ignored, so
		// a consumer learns it at decode time rather than by comparing a
		// picture.
		//
		// Every vertex is in WCS. Mesh is a plain Entity with no normal and no
		// elevation, so there is no object coordinate system to compose and
		// the placement is applied as it arrives.
		private IEnumerable<Primitive> MeshPolygons(Mesh mesh, ulong h, uint flags, Placement place)
		{
			if (mesh.SubdivisionLevel > 0)
			{
				Primitive ignored = Primitive.Warning(
					WarningCodes.MeshSubdivisionIgnored,
					h,
					"MESH carries subdivision level "
						+ mesh.SubdivisionLevel.ToString(CultureInfo.InvariantCulture)
						+ " and the Polygon records beside this one are the base mesh the "
						+ "file stores, one per face. Evaluating the subdivision is a "
						+ "smoothing algorithm that would invent vertices the drawing does "
						+ "not hold, so the level is ignored rather than approximated"
				);
				ignored.Flags = flags;
				yield return ignored;
			}

			List<XYZ> vertices = mesh.Vertices;
			for (int f = 0; f < mesh.Faces.Count; f++)
			{
				int[] face = mesh.Faces[f];
				int n = face == null ? 0 : face.Length;

				// A face list a file controls can say anything, and an index
				// past the end of the vertex list is one entity's worth of bad
				// data rather than a reason to fail the decode. The face is
				// dropped and named, the same way a hatch loop that cannot be
				// a polygon is, and the rest of the mesh still crosses.
				string bad = Unreadable(face, n, vertices.Count);
				if (bad != null)
				{
					Primitive w = Primitive.Warning(
						WarningCodes.MeshFaceUnreadable,
						h,
						"face " + f.ToString(CultureInfo.InvariantCulture)
							+ " of this MESH " + bad + ", so it is not emitted"
					);
					w.Flags = flags;
					yield return w;
					continue;
				}

				// Before the array exists, which is the rule the Spline and
				// LwPolyline arms follow: the bound is a bound on what gets
				// allocated, so counting after the allocation is counting too
				// late.
				CheckPointCount(n, "a Polygon record");

				double[] pts = new double[n * 3];
				for (int i = 0; i < n; i++)
				{
					XYZ at = place.Apply(vertices[face[i]]);
					pts[i * 3] = at.X;
					pts[(i * 3) + 1] = at.Y;
					pts[(i * 3) + 2] = at.Z;
				}

				XYZ normal = FaceNormal(pts);
				yield return Primitive.Polygon(h, flags, pts, null, normal.X, normal.Y, normal.Z);
			}
		}

		// Why one face of a mesh is not a polygon, or null when it is one.
		//
		// The message is built here rather than at the call site so the two
		// conditions cannot drift into one sentence that covers neither: a
		// face of two vertices and a face indexing past the vertex list are
		// different faults in the file and a reader chasing one wants to know
		// which.
		private static string Unreadable(int[] face, int n, int vertices)
		{
			if (n < 3)
			{
				return "lists " + n.ToString(CultureInfo.InvariantCulture)
					+ " vertices and a closed polygon needs three";
			}

			for (int i = 0; i < n; i++)
			{
				if (face[i] < 0 || face[i] >= vertices)
				{
					return "indexes vertex " + face[i].ToString(CultureInfo.InvariantCulture)
						+ " of a list that holds "
						+ vertices.ToString(CultureInfo.InvariantCulture);
				}
			}

			return null;
		}

		// The plane a mesh face lies in, measured off the face itself.
		//
		// Record 9 carries a normal and a MESH carries none, so there is
		// nothing to read and it has to be computed. Not from the placement:
		// the placement's basis is the world frame the transform maps to, and
		// a mesh face standing on its side would be handed +Z, which is a
		// number in the right slot and a false statement about the drawing.
		//
		// Newell's method rather than one cross product, because a mesh face
		// is allowed more than three vertices and is not required to be
		// planar. Newell gives the area-weighted average of the plane the
		// whole loop traces, it degrades to exactly the cross product on a
		// triangle, and it does not pick out three vertices that happen to be
		// nearly collinear and answer with noise.
		//
		// It is computed from the points as emitted, after the placement, so
		// the normal follows the winding the record actually carries. That is
		// what makes a mirrored insertion come out right without a correction
		// of its own: a mirror reverses the traversal, and a normal measured
		// off the reversed traversal reverses with it.
		//
		// A face with no area has no plane, and Unit answers +Z there. That is
		// a name rather than a measurement, and it is the deliberate choice:
		// the alternative is a normal of NaN, which Finite() would turn into a
		// refusal of a face whose vertices are perfectly good numbers.
		private static XYZ FaceNormal(double[] pts)
		{
			int n = pts.Length / 3;
			double nx = 0.0;
			double ny = 0.0;
			double nz = 0.0;

			for (int i = 0; i < n; i++)
			{
				int j = i + 1 == n ? 0 : i + 1;
				double ax = pts[i * 3];
				double ay = pts[(i * 3) + 1];
				double az = pts[(i * 3) + 2];
				double bx = pts[j * 3];
				double by = pts[(j * 3) + 1];
				double bz = pts[(j * 3) + 2];

				nx += (ay - by) * (az + bz);
				ny += (az - bz) * (ax + bx);
				nz += (ax - bx) * (ay + by);
			}

			return Unit(new XYZ(nx, ny, nz));
		}
	}
}
