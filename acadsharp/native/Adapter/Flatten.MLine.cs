using System;
using System.Collections.Generic;
using System.Globalization;
using ACadSharp.Entities;
using ACadSharp.Objects;
using CSMath;
using Viprs.Wire;

// MLINE, which is a path plus a style and lowers to one record 4 per element
// of that style.
//
// This is the shape docs/WIRE.md's "Geometry that needs a lookup" section is
// about. An MLINE carries a centre path and a handle to an MLINESTYLE, and the
// lines a drawing shows are that path offset by each of the style's element
// offsets. The part that can be computed without the style is the centre path,
// and emitting it alone is the exact defect the rule exists to prevent: it is
// a Polyline record with a plausible point count and real coordinates, and no
// field on it says it is the half that did not need the lookup
// (libviprs-dep#87).
//
// So either every element crosses, or nothing does and a warning says why.
//
// The geometry. Every coordinate on an MLINE is world: StartPoint, and per
// vertex Position, Direction (the segment leaving that vertex) and Miter (the
// joint's bisector, which at the two ends of an open path is the segment's
// perpendicular). Normal is the plane the entity is drawn in and decides which
// side a positive offset lies on. For element `e` of the style, in style
// order:
//
//     reference = 0                  for Zero
//               = max(offsets)       for Top
//               = min(offsets)       for Bottom
//     effective = (e.Offset - reference) * ScaleFactor
//     per vertex v:
//         D = unit(v.Direction), M = unit(v.Miter), N = unit(Normal x D)
//         t = effective / dot(M, N)
//         point = v.Position + M * t
//
// The division by dot(M, N) is what makes a bend meet itself. At a joint the
// miter is longer than the offset, by exactly the secant of half the turn, and
// offsetting each segment along its own perpendicular instead leaves a gap at
// every corner.
//
// None of those conventions is mine and none of them is guessed. I confirmed
// all three (the side a positive offset is on, the Top and Bottom references,
// and the miter division) against real_AC1032.dwg, which AutoCAD wrote, in two
// independent ways: ezdxf's MLine.virtual_entities() on a DXF export of it,
// and the per-vertex per-element Segments[i].Parameters[0] that AutoCAD bakes
// into the file, which per the ODA specification is that same `t`. Both agreed
// to every digit on all three of its MLINEs.
//
// The baked parameters stay an oracle and never become a second code path. Two
// ways of computing one shape is how the two drift, and a file is free to
// carry parameters that do not agree with its own style.
//
// What cannot be observed here, and it is worth writing down where the next
// person will look: a dangling style handle. ACadSharp's CadMLineTemplate.build
// assigns Style only when the handle resolves, and otherwise the entity keeps
// MLineStyle.Default, whose AssignDocument then runs TryAdd and hands back the
// document's own "Standard". So a file whose style is missing decodes as though
// it said Standard with offsets +0.5 and -0.5, and nothing at this layer can
// tell the two apart. The substitution is upstream's and lives in the pinned
// version. A later pin that exposes the unresolved handle is where a refusal
// for "the style is not in the file" would go; until then the unresolvable
// states this arm can actually see are a style that holds no elements and a
// path with fewer than two vertices.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		// The style features this version does not draw, all in one place so
		// the warning and the condition cannot drift apart.
		private const MLineStyleFlags MLineCaps =
			MLineStyleFlags.StartSquareCap
			| MLineStyleFlags.StartInnerArcsCap
			| MLineStyleFlags.StartRoundCap
			| MLineStyleFlags.EndSquareCap
			| MLineStyleFlags.EndInnerArcsCap
			| MLineStyleFlags.EndRoundCap;

		private IEnumerable<Primitive> MLinePolylines(
			MLine mline,
			ulong h,
			uint flags,
			Placement place
		)
		{
			List<double> offsets = new List<double>();
			if (mline.Style != null)
			{
				foreach (MLineStyle.Element element in mline.Style.Elements)
				{
					offsets.Add(element.Offset);
				}
			}

			string refusal = MLineRefusal(mline, offsets.Count);
			if (refusal != null)
			{
				yield return MLineRefused(h, flags, refusal);
				yield break;
			}

			// Before any allocation, and once for the whole entity: every
			// element polyline is the same length, so the bound is about the
			// vertex count rather than about the element count.
			CheckPointCount(mline.Vertices.Count, "a Polyline record");

			double reference = MLineReference(mline.Justification, offsets);
			XYZ normal = Unit(mline.Normal);

			// dot(M, N) per vertex, measured once rather than once per element,
			// and checked before anything is emitted so a vertex nothing can be
			// placed at refuses the whole entity rather than half of it.
			double[] denominators = new double[mline.Vertices.Count];
			for (int i = 0; i < mline.Vertices.Count; i++)
			{
				MLine.Vertex v = mline.Vertices[i];
				XYZ side = Unit(XYZ.Cross(normal, Unit(v.Direction)));
				denominators[i] = Unit(v.Miter).Dot(side);
				if (Math.Abs(denominators[i]) < Eps)
				{
					yield return MLineRefused(
						h,
						flags,
						"MLINE vertex " + i.ToString(CultureInfo.InvariantCulture)
							+ " carries a miter parallel to its own segment, so no "
							+ "element can be placed"
					);
					yield break;
				}
			}

			string asked = MLineFeatures(mline.Style);
			if (asked != null)
			{
				Primitive w = Primitive.Warning(
					WarningCodes.MLineStyleFeaturesIgnored,
					h,
					"MLINE style " + mline.Style.Name + " asks for " + asked
						+ " and this version draws the element lines only; the fill, "
						+ "the joint lines and the caps are not emitted"
				);
				w.Flags = flags;
				yield return w;
			}

			// The plane the record names is the entity's own, composed with
			// whatever block transform it arrived under, exactly as the
			// Polyline3D branch of the IPolyline arm does it. The points are
			// not lifted through it: an MLINE's vertices are already world
			// coordinates and lifting one would move it.
			Basis basis = place.WithOcs(mline.Normal, 0.0).Basis;
			bool closed = (mline.Flags & MLineFlags.Closed) == MLineFlags.Closed;

			for (int e = 0; e < offsets.Count; e++)
			{
				double effective = (offsets[e] - reference) * mline.ScaleFactor;
				double[] pts = new double[mline.Vertices.Count * 3];
				for (int i = 0; i < mline.Vertices.Count; i++)
				{
					MLine.Vertex v = mline.Vertices[i];
					XYZ miter = Unit(v.Miter);
					double t = effective / denominators[i];
					XYZ at = place.Apply(
						new XYZ(
							v.Position.X + (miter.X * t),
							v.Position.Y + (miter.Y * t),
							v.Position.Z + (miter.Z * t)
						)
					);
					pts[i * 3] = at.X;
					pts[(i * 3) + 1] = at.Y;
					pts[(i * 3) + 2] = at.Z;
				}

				// Every element under the entity's own handle, which is
				// record 9's MESH precedent: several records for one entity,
				// consecutive, and a consumer that wants the entity back takes
				// the run.
				yield return OnePolyline(
					h,
					flags,
					closed,
					pts,
					new double[0],
					basis.Mirrored,
					basis.Normal
				);
			}
		}

		// Why this MLINE cannot be placed at all, or null when it can.
		//
		// Both conditions are about something the file does not hold rather
		// than about something this build has not got to, so both are 100 with
		// a sentence naming which, and the first word is the DXF kind because
		// the corpus tests read the type back by splitting on the first space.
		private static string MLineRefusal(MLine mline, int elements)
		{
			if (mline.Vertices.Count < 2)
			{
				return "MLINE carries "
					+ mline.Vertices.Count.ToString(CultureInfo.InvariantCulture)
					+ " vertices and a run of parallel lines needs two";
			}

			if (elements < 1)
			{
				return "MLINE names style "
					+ (mline.Style == null ? "(none)" : mline.Style.Name)
					+ " which carries no elements, so there is no offset to place a "
					+ "line at and the path alone is not the entity";
			}

			return null;
		}

		private static Primitive MLineRefused(ulong handle, uint flags, string reason)
		{
			Primitive w = Primitive.Warning(WarningCodes.UnsupportedEntity, handle, reason);
			w.Flags = flags;
			return w;
		}

		// Which of the style's offsets lands on the path the file holds.
		private static double MLineReference(MLineJustification justification, List<double> offsets)
		{
			if (justification == MLineJustification.Zero || offsets.Count == 0)
			{
				return 0.0;
			}

			double reference = offsets[0];
			for (int i = 1; i < offsets.Count; i++)
			{
				bool take = justification == MLineJustification.Top
					? offsets[i] > reference
					: offsets[i] < reference;
				if (take)
				{
					reference = offsets[i];
				}
			}

			return reference;
		}

		// What the style asks for that this version does not draw, as the
		// warning spells it, or null when it asks for none of it.
		private static string MLineFeatures(MLineStyle style)
		{
			if (style == null)
			{
				return null;
			}

			List<string> asked = new List<string>();
			if ((style.Flags & MLineStyleFlags.FillOn) == MLineStyleFlags.FillOn)
			{
				asked.Add("fill");
			}

			if ((style.Flags & MLineStyleFlags.DisplayJoints) == MLineStyleFlags.DisplayJoints)
			{
				asked.Add("joints");
			}

			if ((style.Flags & MLineCaps) != 0)
			{
				asked.Add("caps");
			}

			if (asked.Count == 0)
			{
				return null;
			}

			return string.Join(", ", asked.ToArray());
		}
	}
}
