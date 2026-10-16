using System;
using System.Collections.Generic;
using System.Globalization;
using ACadSharp.Entities;
using CSMath;
using Viprs.Wire;

// IMAGE and PDFUNDERLAY, as record 16 (wire version 3).
//
// Both draw something that is not in the drawing: a raster, or a page of a PDF,
// that the drawing only names. ADR 0002 refused both for a policy, not for
// geometry, and the policy is unchanged: this decoder opens no path a drawing
// names. What the refusal threw away along with the resource was the drawing's
// own geometry, which is a frame (where the picture goes, how big, how turned
// and how clipped) and a label (what it is called). A consumer that wants the
// picture goes and fetches it under a policy of its own; one that does not
// draws the frame and the label. The record is how both get the frame.
//
// The name is carried as an opaque string and nothing here opens it. Not to
// stat it, not to read a header out of it, not to find out whether it is
// there. tests/test_placement_records.py holds that: the fixture names an
// absolute path that does not exist, and the record has to come out identical
// whether or not anything went looking.
//
// The frame is the insertion point and two vectors that carry its size, its
// rotation and its skew together, so a consumer draws it as the parallelogram
// from the insertion point along u and along v without ever reconstructing an
// angle. The clip is in the frame's own coordinates and a consumer does two
// multiplies and two adds per vertex:
//
//     world = insert + u * x + v * y
//
// IMAGE. The vectors are the stored per-pixel ones scaled up by the pixel
// size, so u and v span the whole picture and x, y are fractions of it. The
// producer has already got out of pixel space, which nobody downstream should
// have to know about: pixel rows run the other way from V and the origin
// sits half a pixel outside the first pixel, so a vertex (px, py) is
//
//     x = (px + 0.5) / size_u        y = (size_v - py - 0.5) / size_v
//
// which is Flatten.Masks.cs's mapping divided through by the size, and was
// confirmed against ezdxf there. A rectangular clip stores two opposite
// corners and means four.
//
// PDFUNDERLAY. The file does not say how big the page is, so the size is not
// ours to give: size_u and size_v are zero, which a consumer reads as
// "unknown", and u and v are one underlay unit long times the entity's scale,
// turned by its rotation about its normal. The clip is in underlay units
// rather than fractions, and the same formula holds because u and v are
// per-unit here. ADR 0003 said fractions for both kinds; that cannot be done
// for an underlay, and this is the smallest departure that keeps one formula.
// The unit of the stored clip is read from the field's documentation, not
// from a drawing made by AutoCAD with a clipped underlay, because the corpus
// has none: that is the part of this arm nobody has been able to check
// against a second implementation, and docs/WIRE.md says so.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		private const uint PlacementImage = 0u;
		private const uint PlacementPdf = 1u;

		private IEnumerable<Primitive> ImagePlacement(
			RasterImage image,
			ulong h,
			uint flags,
			Placement place
		)
		{
			double sw = image.Size.X;
			double sh = image.Size.Y;
			if (sw <= 0.0 || sh <= 0.0)
			{
				Primitive w = Primitive.Warning(
					WarningCodes.UnsupportedEntity,
					h,
					"IMAGE has a pixel size of "
						+ sw.ToString(CultureInfo.InvariantCulture)
						+ " by "
						+ sh.ToString(CultureInfo.InvariantCulture)
						+ ", so its frame has no area to place"
				);
				w.Flags = flags;
				yield return w;
				yield break;
			}

			XYZ origin = place.Apply(image.InsertPoint);
			XYZ u = place.Apply(image.InsertPoint + (image.UVector * sw)) - origin;
			XYZ v = place.Apply(image.InsertPoint + (image.VVector * sh)) - origin;

			IList<XY> stored = image.ClipBoundaryVertices;
			int held = stored == null ? 0 : stored.Count;
			double[] clip = null;
			if (image.ClippingState && held > 0)
			{
				bool rectangle = image.ClipType == ClipType.Rectangular && held == 2;
				bool repeats = !rectangle
					&& held > 1
					&& stored[held - 1].X == stored[0].X
					&& stored[held - 1].Y == stored[0].Y;
				int n = rectangle ? 4 : (repeats ? held - 1 : held);
				CheckPointCount(n, "a Placement record's clip");
				clip = new double[n * 2];
				for (int i = 0; i < n; i++)
				{
					XY pixel = rectangle ? Corner(stored[0], stored[1], i) : stored[i];
					clip[i * 2] = (pixel.X + 0.5) / sw;
					clip[(i * 2) + 1] = (sh - pixel.Y - 0.5) / sh;
				}
			}

			yield return Primitive.Placement(
				h,
				flags,
				PlacementImage,
				new double[] { origin.X, origin.Y, origin.Z },
				new double[] { u.X, u.Y, u.Z },
				new double[] { v.X, v.Y, v.Z },
				sw,
				sh,
				clip,
				image.Definition == null ? string.Empty : image.Definition.FileName
			);
		}

		private IEnumerable<Primitive> UnderlayPlacement(
			PdfUnderlay underlay,
			ulong h,
			uint flags,
			Placement place
		)
		{
			// The insertion point is world, like the rest of the frame's
			// vectors on IMAGE. The normal and the rotation only say which way
			// the page faces and how it is turned in that plane.
			XYZ ox = ArbitraryAxisX(underlay.Normal);
			XYZ oy = XYZ.Cross(Unit(underlay.Normal), ox);
			double c = Math.Cos(underlay.Rotation);
			double s = Math.Sin(underlay.Rotation);
			XYZ u0 = ((ox * c) + (oy * s)) * underlay.XScale;
			XYZ v0 = ((ox * -s) + (oy * c)) * underlay.YScale;

			XYZ origin = place.Apply(underlay.InsertPoint);
			XYZ u = place.Apply(underlay.InsertPoint + u0) - origin;
			XYZ v = place.Apply(underlay.InsertPoint + v0) - origin;

			IList<XY> stored = underlay.ClipBoundaryVertices;
			int held = stored == null ? 0 : stored.Count;
			double[] clip = null;
			if (underlay.Flags.HasFlag(UnderlayDisplayFlags.ClippingOn) && held > 0)
			{
				bool rectangle = held == 2;
				bool repeats = !rectangle
					&& held > 1
					&& stored[held - 1].X == stored[0].X
					&& stored[held - 1].Y == stored[0].Y;
				int n = rectangle ? 4 : (repeats ? held - 1 : held);
				CheckPointCount(n, "a Placement record's clip");
				clip = new double[n * 2];
				for (int i = 0; i < n; i++)
				{
					XY at = rectangle ? Corner(stored[0], stored[1], i) : stored[i];
					clip[i * 2] = at.X;
					clip[(i * 2) + 1] = at.Y;
				}
			}

			yield return Primitive.Placement(
				h,
				flags,
				PlacementPdf,
				new double[] { origin.X, origin.Y, origin.Z },
				new double[] { u.X, u.Y, u.Z },
				new double[] { v.X, v.Y, v.Z },
				0.0,
				0.0,
				clip,
				underlay.Definition == null ? string.Empty : underlay.Definition.File
			);
		}
	}
}
