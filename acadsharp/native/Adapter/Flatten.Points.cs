using System.Collections.Generic;
using ACadSharp.Entities;
using CSMath;
using Viprs.Wire;

// POINT, as record 14 (wire version 3).
//
// A POINT is a position and nothing else, so no shape wire version 2 defines
// can hold one, and lowering it to a one-vertex polyline would be a record a
// consumer draws as a stroke of no length, which is nothing. It had no home
// until the Point record, and it was 40 of the 88 refusals on a real
// drawing (libviprs-dep#83).
//
// Three decisions, each with a plausible opposite.
//
//  1. The location is NOT lifted. ADR 0003 said it was, by analogy with the
//     other planar entities, and the analogy is wrong for this one: DXF group
//     10 on a POINT is documented WCS, ACadSharp's own comment on Location
//     says "Point location(in WCS)", and the extrusion on a POINT exists to
//     orient the marker and its thickness, not to place it. So the position
//     goes through the placement exactly as stored. An arm that lifted it
//     would move a point with a non-Z extrusion a long way and still emit a
//     perfectly plausible one, which is the kind of mistake the fixture's
//     fourth point is shaped to catch. The same reasoning is already written
//     down for LEADER in Flatten.Leaders.cs.
//
//  2. The record still carries the plane. The marker is a glyph drawn in the
//     plane the extrusion names, so a consumer that draws anything but a dot
//     needs to know which. The normal and the rotation come off the same
//     measured basis an ARC's do, which is what keeps them right under a
//     rotated, tilted or mirrored insertion: the rotation is the angle of the
//     marker's x axis measured counter-clockwise about the record's normal,
//     and a reflection reverses both the normal's side and what
//     counter-clockwise means, so the angle follows it the way an arc's ends
//     do.
//
//  3. PDMODE and PDSIZE are not here. They are header variables that say which
//     glyph AutoCAD draws and how big, which is a rendering decision made by
//     whoever is drawing, and this boundary's job is to say where the point
//     is. Thickness is not here either: the record is flat, as every record
//     on this wire is.
namespace Viprs.Cad
{
	internal sealed partial class Flattener
	{
		private IEnumerable<Primitive> PointRecord(
			Point point,
			ulong h,
			uint flags,
			Placement place
		)
		{
			XYZ at = place.Apply(point.Location);

			Placement plane = place.WithOcs(point.Normal, 0.0);
			Basis basis = plane.Basis;
			double rotation = basis.Mirrored
				? basis.Turn - point.Rotation
				: point.Rotation + basis.Turn;

			yield return Primitive.PointAt(
				h,
				flags,
				at.X,
				at.Y,
				at.Z,
				basis.Normal.X,
				basis.Normal.Y,
				basis.Normal.Z,
				rotation
			);
		}
	}
}
