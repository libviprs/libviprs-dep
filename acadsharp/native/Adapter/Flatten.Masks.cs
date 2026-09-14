using System.Collections.Generic;
using System.Globalization;
using ACadSharp.Entities;
using CSMath;
using Viprs.Wire;

// WIPEOUT, the one entity on this wire whose record hides something rather
// than draws it.
//
// A wipeout is an image with no image. Upstream models it as CadWipeoutBase,
// shared with RasterImage, and everything that makes a raster image a picture
// is absent: there is no definition, no file name and no pixels. What is left
// is a frame and a boundary, and both of those are entirely in the drawing.
// So this is not the refusal IMAGE and PDFUNDERLAY are. Those name a file
// this decoder will not open; a wipeout names nothing, and refusing it was
// only ever "nobody has done it yet", which is what docs/adr/0002 recorded and
// what this file closes.
//
// It matters more than one entity usually does, and the direction is the
// reason. Dropping a wipeout does not lose a shape, it reveals whatever the
// drawing put underneath it, and a drawing that shows something it was drawn
// to hide is a plausible wrong drawing rather than an obviously incomplete
// one. The blast radius is the area it covers and not the one entity it is.
//
// Two things decide what the record says.
//
//  1. The boundary is in the image's own pixel space, and getting out of it
//     is the whole of the geometry here. Pixel rows run the opposite way from
//     VVector and the pixel origin sits half a pixel outside the first pixel,
//     so the mapping is
//
//         wcs(px, py) = InsertPoint + UVector * (px + 0.5)
//                                   + VVector * (Size.Y - py - 0.5)
//
//     An implementation that drops the row flip emits a boundary with the
//     right corners reflected about the middle of the frame, which is exactly
//     right on a rectangle and wrong everywhere else, so a corpus of
//     rectangular wipeouts cannot tell the two apart. tests/fixtures'
//     g13_wipeout.dwg is asymmetric for that reason, and the mapping is
//     pinned to what ezdxf answers rather than to this comment: a fixture
//     this generator writes and reads back agrees with itself whichever way
//     the flip goes, so the convention needed a second implementation to
//     confirm it and got one.
//
//  2. Record 9 cannot say "this one covers". There is no spare field, and the
//     prologue's spare bits are a semantic change under an unchanged wire
//     version, which is the single thing the version number and the ABI
//     fingerprint exist to prevent: a consumer compiled yesterday reads the
//     same bytes and means something else by them. Warning 112 is the
//     sanctioned channel instead, because docs/WIRE.md promises a consumer
//     skips a code it does not know, so an old consumer fills the boundary
//     like any other record 9. That still hides what is under it. A consumer
//     that does know 112 paints its background colour or clips. Neither shows
//     the content, and choosing the failure direction is the point.
//
// A wipeout's frame is documented WCS on all three vectors, so nothing here
// goes through WithOcs: the entity carries no extrusion at all, the same way
// a MESH does not, and record 9's normal is measured off the points as
// emitted.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		// WIPEOUT as warning 112 and then one closed Polygon.
		//
		// Two records under one handle, in that order, which is MESH's
		// precedent for both halves: several records may share a handle, and
		// the warning that qualifies them comes first so a consumer reading
		// forward knows before it draws rather than after.
		private IEnumerable<Primitive> WipeoutPolygon(
			Wipeout wipeout,
			ulong h,
			uint flags,
			Placement place
		)
		{
			IList<XY> stored = wipeout.ClipBoundaryVertices;
			int held = stored == null ? 0 : stored.Count;

			// A rectangular clip stores two opposite corners and means four.
			// Upstream's own documentation on ClipBoundaryVertices says so,
			// and the default it describes, (-0.5, -0.5) to (size - 0.5), is
			// the whole image. The count is checked rather than the type
			// alone: a file is the one saying which of the two it is, and a
			// rectangle that does not carry exactly two corners is read as the
			// vertex list it actually holds rather than trusted about itself.
			bool rectangle = wipeout.ClipType == ClipType.Rectangular && held == 2;

			// The DXF form of a polygonal boundary repeats the first vertex at
			// the end and the DWG form does not, so a drawing that has been
			// through a DXF carries one more vertex than it has corners.
			// Record 9 closes implicitly and does not repeat the first vertex,
			// so the duplicate would be a coincident pair on the wire. The
			// comparison is exact and it is made on what the file holds,
			// before any mapping, the same rule SOLID's triangle uses: it is a
			// copy of a stored value rather than a near miss.
			bool repeats = !rectangle
				&& held > 1
				&& stored[held - 1].X == stored[0].X
				&& stored[held - 1].Y == stored[0].Y;

			int n = rectangle ? 4 : (repeats ? held - 1 : held);

			if (n < 3)
			{
				// A file controls this number and three is the fewest a closed
				// boundary can be. Refused rather than emitted, and the
				// direction is the safe one for once: a mask with no area
				// hides nothing, so nothing is revealed by leaving it out.
				Primitive thin = Primitive.Warning(
					WarningCodes.UnsupportedEntity,
					h,
					"WIPEOUT boundary lists "
						+ n.ToString(CultureInfo.InvariantCulture)
						+ " vertices and a mask needs three, so it is not emitted"
				);
				thin.Flags = flags;
				yield return thin;
				yield break;
			}

			// Before the array exists, and on the count the record will carry.
			// Both numbers are the file's: a polygonal boundary says how many
			// vertices follow, and a caller's max_polyline_points is what
			// stops a drawing asking for an array of them.
			CheckPointCount(n, "a Polygon record");

			double[] pts = new double[n * 3];
			for (int i = 0; i < n; i++)
			{
				XY pixel = rectangle ? Corner(stored[0], stored[1], i) : stored[i];
				XYZ at = place.Apply(ToWorld(wipeout, pixel));
				pts[i * 3] = at.X;
				pts[(i * 3) + 1] = at.Y;
				pts[(i * 3) + 2] = at.Z;
			}

			// Measured off the emitted points, exactly as a MESH face's is,
			// because a wipeout carries no normal of its own. That also makes
			// a mirrored insertion come out right with no correction: a mirror
			// reverses the traversal and a normal measured off the reversed
			// traversal reverses with it.
			XYZ normal = FaceNormal(pts);

			Primitive masks = Primitive.Warning(
				WarningCodes.PolygonMasks,
				h,
				"WIPEOUT is a mask, and the Polygon record beside this one under the same "
					+ "handle is the boundary it masks rather than a face: it hides "
					+ "whatever the stream drew before it, inside that boundary. Wire "
					+ "version 2's record 9 has no field to say that, so this code does, "
					+ "and a consumer that does not know it fills the boundary like any "
					+ "other polygon, which still covers what is underneath"
			);
			masks.Flags = flags;
			yield return masks;

			// No bulge array. A clip boundary is a run of straight edges and
			// record 9 leaves the array out entirely when every span is, which
			// is the same thing SOLID and MESH do.
			yield return Primitive.Polygon(
				h,
				flags,
				pts,
				null,
				normal.X,
				normal.Y,
				normal.Z
			);
		}

		// One corner of a rectangular clip, in traversal order.
		//
		// The two stored corners are opposite, so the four are (a.x, a.y),
		// (b.x, a.y), (b.x, b.y) and (a.x, b.y). Taking them in storage order
		// and calling it a polygon gives the diagonal, which is two vertices
		// and not a shape.
		private static XY Corner(XY a, XY b, int i)
		{
			switch (i)
			{
				case 0: return new XY(a.X, a.Y);
				case 1: return new XY(b.X, a.Y);
				case 2: return new XY(b.X, b.Y);
				default: return new XY(a.X, b.Y);
			}
		}

		// One pixel-space vertex in world coordinates.
		//
		// UVector and VVector are one pixel each and documented WCS, Size is
		// the image's size in pixels, and the two halves of the offset are the
		// two things that are easy to leave out. The half pixel puts the point
		// at the centre of a pixel rather than at its corner, and the
		// subtraction from Size.Y is the row direction: pixel row 0 is the top
		// and VVector points up the visual left side from the insertion point,
		// which is the bottom.
		private static XYZ ToWorld(Wipeout wipeout, XY pixel)
		{
			double u = pixel.X + 0.5;
			double v = wipeout.Size.Y - pixel.Y - 0.5;
			return wipeout.InsertPoint + (wipeout.UVector * u) + (wipeout.VVector * v);
		}
	}
}
