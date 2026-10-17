using System;
using CSMath;

// A planar curve carried through a transform that does not keep it round
// (libviprs/acadsharp-rs-tests#64).
//
// A circle, an arc or an ellipse is a centre and two conjugate semi-diameters
// in its own plane: p(t) = c + u cos t + v sin t. An affine map takes
// conjugate semi-diameters to conjugate semi-diameters, so mapping u and v and
// rebuilding the curve from the images is exact, and it needs no tessellation
// and no centre recovered from an approximation. When the images are still
// perpendicular and equal in length the result is still a circle and the
// caller never gets here. When they are not, the image is an ellipse, and this
// finds its principal axes.
//
// Wire version 2 kept the circle and said so with warning 106, on the grounds
// that the record had no transform to carry. That undersold it: the shim
// holds the transform, the Ellipse record is in the wire already, and saying
// the shape is stretched is a poorer answer than writing the stretched shape.
// Schema 1 writes the ellipse (docs/CANONICAL_SCHEMA.md), so does every other
// reader, and a consumer that gets the circle has been handed a shape that is
// not in the drawing.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		// The image of a planar curve, as an ellipse in the plane its own
		// semi-diameters span.
		internal struct ImageEllipse
		{
			// Centre, in world coordinates.
			public XYZ Center;

			// The plane, as a unit vector, oriented so that the image of u runs
			// towards the image of v counter-clockwise about it. No reflection
			// has to be corrected for: the orientation comes from the images, so
			// a mirror gives the normal that keeps the curve's sense of rotation.
			public XYZ Normal;

			// The major semi-axis, as a world vector from the centre.
			public XYZ Major;

			// Minor over major, in (0, 1].
			public double Ratio;

			// What to subtract from a parameter measured against u and v to get
			// the parameter against Major and the axis perpendicular to it.
			public double Theta;

			// The two images are parallel, or one of them is nothing: the curve
			// has collapsed onto a line or a point and no ellipse names it.
			public bool Degenerate;
		}

		// A two by two singular value decomposition, closed form, of the images
		// of u and v expressed in the arbitrary-axis frame of their own normal:
		//
		//     [u v] = R(phi) diag(s1, s2) R(theta)^T
		//
		// R(phi)'s first column is the major direction, s1 and s2 are the
		// semi-axis lengths, and a parameter t against u and v becomes t - theta
		// against the principal axes. The normal is the cross product of the
		// images, so the determinant is positive and s2 is never negative.
		internal static ImageEllipse ImageOfCurve(Placement place, XYZ centre, XYZ u, XYZ v)
		{
			ImageEllipse r = new ImageEllipse();

			XYZ o = place.Apply(XYZ.Zero);
			XYZ wu = place.Apply(u) - o;
			XYZ wv = place.Apply(v) - o;
			r.Center = place.Apply(centre);
			r.Ratio = 1.0;

			XYZ cross = XYZ.Cross(wu, wv);
			double reach = Math.Max(wu.GetLength(), wv.GetLength());
			double area = cross.GetLength();
			if (!(area > Eps * reach * reach))
			{
				r.Normal = XYZ.AxisZ;
				r.Degenerate = true;
				return r;
			}

			XYZ n = Unit(cross);
			XYZ ax = ArbitraryAxisX(n);
			XYZ ay = XYZ.Cross(n, ax);
			r.Normal = n;

			double ux = wu.Dot(ax);
			double uy = wu.Dot(ay);
			double vx = wv.Dot(ax);
			double vy = wv.Dot(ay);

			double e = (ux + vy) / 2.0;
			double f = (ux - vy) / 2.0;
			double g = (uy + vx) / 2.0;
			double h = (uy - vx) / 2.0;
			double q = Math.Sqrt((e * e) + (h * h));
			double s = Math.Sqrt((f * f) + (g * g));
			double s1 = q + s;
			double s2 = q - s;
			double a1 = Math.Atan2(h, e);
			double a2 = Math.Atan2(g, f);
			double phi = (a1 + a2) / 2.0;
			r.Theta = (a1 - a2) / 2.0;

			if (!(s1 > 0.0) || !(Math.Abs(s2) > Eps * s1))
			{
				r.Degenerate = true;
				return r;
			}

			double cp = Math.Cos(phi);
			double sp = Math.Sin(phi);
			r.Major = new XYZ(
				s1 * ((cp * ax.X) + (sp * ay.X)),
				s1 * ((cp * ax.Y) + (sp * ay.Y)),
				s1 * ((cp * ax.Z) + (sp * ay.Z))
			);
			r.Ratio = Math.Abs(s2) / s1;
			return r;
		}
	}
}
