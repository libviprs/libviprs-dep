using System;
using System.Collections.Generic;
using System.Globalization;
using ACadSharp;
using ACadSharp.Entities;
using ACadSharp.Objects;
using ACadSharp.Tables;
using CSMath;
using Viprs.Wire;

// MULTILEADER (libviprs-dep#86): leader lines, a landing, content, and an
// arrowhead that is a glyph the style names.
//
// This is the one annotation composite that really does expand. LEADER is a
// vertex run and nothing else, MLINE is a set of parallel runs, and both
// lowered onto record 4 without handing an entity to anything. A MULTILEADER's
// content can be a block, and a block is entities, so it goes where DIMENSION's
// picture and HATCH's boundary go: onto Walk's own stack, one depth deeper,
// under the same bound an INSERT is held to. Expanding it from inside Map would
// put a recursion the file controls on the CLR stack, and a MULTILEADER whose
// block holds an INSERT of a block holding another MULTILEADER is exactly that
// file. Nothing in this file calls Map; it yields items for the walk to visit.
//
// Everything is read from the context data, which is where the geometry lives
// (the entity's own properties are style overrides). Five decisions, each with
// a plausible opposite.
//
//  1. Coordinates are world and are NOT lifted. The leader lines, the landing,
//     the text location and the block location are documented WCS in the
//     context data, and the entity carries no extrusion of its own. This is
//     LEADER's first decision for the same reason.
//
//  2. The landing is drawn from the root's connection point along its
//     direction for its landing distance, when the style enables a landing.
//     The connection point is the point the leader lines meet and the landing
//     runs on from it to the content. That is how the format documents the
//     three fields (last leader point, dogleg vector, dogleg length), and it
//     is the part of this file that has not been compared against a second
//     implementation: the corpus holds it from the writer and from one real
//     drawing, which is to say from the same reading of the same fields.
//
//  3. Text content is one record 10 at the text location, with the height and
//     rotation the context stores, and the label goes through the same escape
//     resolution every other record 10 gets. The attachment (which corner of
//     the text the location is) is not resolved, which is what the MTEXT arm
//     does as well.
//
//  4. Block content is placed by its stored location, rotation and scale, with
//     the block's base point taken out first, the way InsertMatrix does for an
//     INSERT. The one difference is that the location is world rather than in
//     an object coordinate system, so the extrusion orients the block without
//     moving it.
//
//  5. What is not drawn is said, once per entity and not once per feature. The
//     arrowhead is a glyph at a size the style sets, and neither is in the
//     file as geometry, so it is code 114 exactly as LEADER's is. A text frame
//     and a break in a leader line are boxes and gaps AutoCAD works out from
//     text extents and a style, so they are code 116, which names which. A
//     spline path is refused whole, as LEADER's is, because its vertices are
//     control points for a curve the file does not hold as a curve.
//
// A MULTILEADER with no leader lines and no content is not silently empty: it
// says so on 100, because "this entity produced nothing" is the one thing a
// consumer cannot otherwise tell from "this entity was never here".
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		private IEnumerable<Pending> MultiLeaderBody(MultiLeader ml, Pending item)
		{
			ulong h = ml.Handle;
			uint flags = item.Depth > 0 ? FlagFromBlock : 0u;
			Placement place = item.Placement;
			MultiLeaderObjectContextData ctx = ml.ContextData;

			if (ml.PathType == MultiLeaderPathType.Spline)
			{
				yield return Record(
					WarningCodes.UnsupportedEntity,
					h,
					flags,
					item.Depth,
					"MULTILEADER is a spline-path leader and its curve is not in the file as a "
						+ "curve: a polyline through its control points would be a tessellation "
						+ "by another name, so it is not emitted"
				);
				yield break;
			}

			bool drawLines = ml.PathType != MultiLeaderPathType.Invisible;
			bool hasText = ctx.HasTextContents && !string.IsNullOrEmpty(ctx.TextLabel);
			BlockRecord block = ctx.HasContentsBlock ? ctx.BlockContent : null;

			int lines = 0;
			bool arrowhead = false;
			int breaks = 0;
			foreach (MultiLeaderObjectContextData.LeaderRoot root in ctx.LeaderRoots)
			{
				breaks += root.BreakStartEndPointsPairs.Count;
				foreach (MultiLeaderObjectContextData.LeaderLine line in root.Lines)
				{
					if (line.Points.Count < 2)
					{
						continue;
					}

					lines++;
					breaks += line.StartEndPoints.Count;
					double size = line.ArrowheadSize > 0.0 ? line.ArrowheadSize : ctx.ArrowheadSize;
					arrowhead = arrowhead || (drawLines && size > 0.0);
				}
			}

			if (arrowhead)
			{
				yield return Record(
					WarningCodes.ArrowheadNotDrawn,
					h,
					flags,
					item.Depth,
					"MULTILEADER asks for an arrowhead at the start of its leader lines and this "
						+ "version draws the lines only; the arrowhead is a glyph the style names "
						+ "and it is not emitted"
				);
			}

			List<string> ignored = new List<string>();
			if (hasText && ml.TextFrame)
			{
				ignored.Add("the frame round its text");
			}

			if (breaks > 0)
			{
				ignored.Add(
					breaks.ToString(CultureInfo.InvariantCulture) + " break(s) in its leader lines"
				);
			}

			if (ml.BlockAttributes.Count > 0)
			{
				ignored.Add("the attribute text of its block");
			}

			if (ml.ContentType == LeaderContentType.Tolerance)
			{
				ignored.Add("its tolerance content");
			}

			if (ignored.Count > 0)
			{
				yield return Record(
					WarningCodes.MultiLeaderFeaturesIgnored,
					h,
					flags,
					item.Depth,
					"MULTILEADER carries " + string.Join(", ", ignored.ToArray())
						+ ", which this version does not draw; the geometry beside this warning "
						+ "is what the file holds"
				);
			}

			int emitted = 0;
			Basis basis = place.Basis;
			foreach (MultiLeaderObjectContextData.LeaderRoot root in ctx.LeaderRoots)
			{
				if (!drawLines)
				{
					break;
				}

				foreach (MultiLeaderObjectContextData.LeaderLine line in root.Lines)
				{
					int n = line.Points.Count;
					if (n < 2)
					{
						continue;
					}

					// Before the list exists, the rule every arm that allocates
					// from a count a file controls follows.
					CheckPointCount(n, "a Polyline record");
					List<double> pts = new List<double>(n * 3);
					for (int i = 0; i < n; i++)
					{
						Append(pts, place, line.Points[i]);
					}

					Primitive polyline = Primitive.Polyline(
						h,
						flags,
						false,
						pts.ToArray(),
						null,
						basis.Normal.X,
						basis.Normal.Y,
						basis.Normal.Z
					);
					emitted++;
					yield return new Pending { Record = polyline, Depth = item.Depth };
				}

				double landing = root.LandingDistance > 0.0 ? root.LandingDistance : ml.LandingDistance;
				if (ml.EnableLanding && landing > 0.0 && root.Direction.GetLength() >= Eps)
				{
					XYZ from = root.ConnectionPoint;
					XYZ to = from + (Unit(root.Direction) * landing);
					XYZ a = place.Apply(from);
					XYZ b = place.Apply(to);
					Primitive stroke = Primitive.Line(h, flags, a.X, a.Y, a.Z, b.X, b.Y, b.Z);
					emitted++;
					yield return new Pending { Record = stroke, Depth = item.Depth };
				}
			}

			if (hasText)
			{
				XYZ at = place.Apply(ctx.TextLocation);
				Primitive text = Primitive.TextAt(
					h,
					flags,
					at.X,
					at.Y,
					at.Z,
					ctx.TextHeight * basis.ScaleY,
					ctx.TextRotation + basis.Turn,
					TextForms.Resolve(ctx.TextLabel, true)
				);
				emitted++;
				yield return new Pending { Record = text, Depth = item.Depth };
			}

			if (block != null)
			{
				XYZ basePoint = block.BlockEntity == null ? XYZ.Zero : block.BlockEntity.BasePoint;
				Matrix4 local = Transform.CreateTranslation(ctx.BlockContentLocation).Matrix
					* ObjectToWorld(ctx.BlockContentNormal)
					* Transform.CreateRotation(XYZ.AxisZ, ctx.BlockContentRotation).Matrix
					* Transform.CreateScaling(ctx.BlockContentScale).Matrix
					* Transform.CreateTranslation(XYZ.Zero - basePoint).Matrix;
				Placement composed = new Placement(Compose(place.Matrix, local));
				foreach (Entity e in block.Entities)
				{
					emitted++;
					yield return new Pending
					{
						Entity = e,
						Placement = composed,
						Depth = item.Depth + 1,
					};
				}
			}

			if (emitted == 0)
			{
				yield return Record(
					WarningCodes.UnsupportedEntity,
					h,
					flags,
					item.Depth,
					"MULTILEADER carries no leader lines and no content, so there is nothing to draw"
				);
			}
		}

		private static Pending Record(uint code, ulong handle, uint flags, int depth, string message)
		{
			Primitive w = Primitive.Warning(code, handle, message);
			w.Flags = flags;
			return new Pending { Record = w, Depth = depth };
		}
	}
}
