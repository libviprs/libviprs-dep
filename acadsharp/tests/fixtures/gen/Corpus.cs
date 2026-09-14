using System;
using System.Collections.Generic;
using System.IO;
using ACadSharp;
using ACadSharp.Blocks;
using ACadSharp.Entities;
using ACadSharp.IO;
using ACadSharp.Objects;
using ACadSharp.Tables;
using CSMath;

namespace Viprs.Cad.Fixtures;

// The G1.3 corpus: one file per entity kind the adapter flattens, plus the
// files that exist only to be refused.
//
// Written with ACadSharp's own DwgWriter and committed, for the same reason
// the spike committed its two: a DWG header carries creation and update
// timestamps, so a rerun produces different bytes and an expectation compared
// against a freshly written file would be comparing two different files.
public static class Corpus
{
	public const string LayerName = "VIPRS_G13";
	public const string BlockName = "VIPRS_G13_BLOCK";
	public const string XrefBlockName = "VIPRS_G13_XREF";
	public const string XrefPath = "../not-resolved/other.dwg";

	// The same external reference with a path long enough to push the warning
	// this library writes about it past a max_string_bytes a caller can set.
	//
	// The filler is a two-byte character on purpose. The byte budget a 4096-byte
	// bound leaves for it is odd, so a truncation that counted bytes rather than
	// characters would cut one of these in half and leave a lone lead byte at the
	// end of a message that is supposed to be UTF-8.
	public const int LongXrefPathBytes = 8192;
	public const string LongXrefPathPrefix = "../not-resolved/";
	public const string LongXrefPathSuffix = ".dwg";
	public const char LongXrefPathFiller = '\u00e9';

	public static string LongXrefPath()
	{
		int fixedBytes = LongXrefPathPrefix.Length + LongXrefPathSuffix.Length;
		int fillers = (LongXrefPathBytes - fixedBytes) / 2;
		return LongXrefPathPrefix
			+ new string(LongXrefPathFiller, fillers)
			+ LongXrefPathSuffix;
	}

	// How many times the hostile file inserts the same block. The number is
	// the issue's, and the amplification benchmark is what it produces.
	public const int HostileInsertCount = 10000;

	// Nesting for the over-depth file. The test sets max_block_depth to
	// DeepBlockDepth - 1 so the file is one level past the bound, and the
	// mutation raises it past DeepBlockDepth so the same test has to pass.
	public const int DeepBlockDepth = 6;

	public const int WidePolylinePoints = 4096;
	public const int LongTextBytes = 8192;

	private static CadDocument NewDoc()
	{
		CadDocument doc = new CadDocument();
		doc.Layers.Add(new Layer(LayerName));
		return doc;
	}

	private static Layer L(CadDocument doc)
	{
		return doc.Layers[LayerName];
	}

	private static void Write(CadDocument doc, string path)
	{
		DwgWriter.Write(path, doc);
	}

	// `only` writes just the fixtures whose name contains it, so a rerun does
	// not have to rewrite the slow ones. Every DWG carries a timestamp, so a
	// rewrite is a new file even when the content is identical, and the
	// expectations are pinned to the digest.
	public static IEnumerable<string> WriteAll(string dir, string only = null)
	{
		Directory.CreateDirectory(dir);
		List<string> written = new List<string>();

		foreach (KeyValuePair<string, Action<string>> kv in Writers())
		{
			if (!string.IsNullOrEmpty(only) && kv.Key.IndexOf(only, StringComparison.Ordinal) < 0)
			{
				continue;
			}

			string path = Path.Combine(dir, kv.Key);
			kv.Value(path);
			written.Add(kv.Key);
		}

		return written;
	}

	public static IEnumerable<KeyValuePair<string, Action<string>>> Writers()
	{
		yield return Pair("g13_line.dwg", WriteLine);
		yield return Pair("g13_polyline.dwg", WritePolyline);
		yield return Pair("g13_arc.dwg", WriteArc);
		yield return Pair("g13_circle.dwg", WriteCircle);
		yield return Pair("g13_ellipse.dwg", WriteEllipse);
		yield return Pair("g13_spline.dwg", WriteSpline);
		yield return Pair("g13_text.dwg", WriteText);
		yield return Pair("g13_insert.dwg", WriteInsert);
		yield return Pair("g13_dimension.dwg", WriteDimension);
		yield return Pair("g13_hatch.dwg", WriteHatch);
		yield return Pair("g13_unsupported.dwg", WriteUnsupported);
		// The refused-entity corpus. One file per kind the flattener does not
		// emit today, written so a comparison against another reader has a small
		// input to run rather than only the 341-record real drawing.
		yield return Pair("g13_point.dwg", WritePoint);
		yield return Pair("g13_solid.dwg", WriteSolidQuad);
		yield return Pair("g13_ray_xline.dwg", WriteRayXline);
		yield return Pair("g13_polyface_mesh.dwg", WritePolyfaceMesh);
		yield return Pair("g13_polygon_mesh.dwg", WritePolygonMesh);
		yield return Pair("g13_mesh.dwg", WriteMesh);
		yield return Pair("g13_mesh_bad_faces.dwg", WriteMeshBadFaces);
		yield return Pair("g13_mline.dwg", WriteMLine);
		yield return Pair("g13_tolerance.dwg", WriteTolerance);
		yield return Pair("g13_xref.dwg", WriteXref);
		yield return Pair("g13_xref_long.dwg", WriteXrefLong);
		yield return Pair("g13_nonuniform.dwg", WriteNonUniform);
		yield return Pair("g13_two_entities.dwg", WriteTwoEntities);
		yield return Pair("g13_deep_blocks.dwg", WriteDeepBlocks);
		yield return Pair("g13_slot.dwg", WriteSlot);
		yield return Pair("g13_slot_block.dwg", WriteSlotBlock);
		yield return Pair("g13_mirrored_bulge.dwg", WriteMirroredBulge);
		yield return Pair("g13_ocs_plane.dwg", WriteOcsPlane);
		yield return Pair("g13_ocs_mirror.dwg", WriteOcsMirror);
		yield return Pair("g13_ocs_rotated.dwg", WriteOcsRotated);
		yield return Pair("g13_ocs_skew.dwg", WriteOcsSkew);
		yield return Pair("g13_nan_bulge.dwg", WriteNanBulge);
		yield return Pair("g13_bad_extents.dwg", WriteBadExtents);
		yield return Pair("g13_empty_view.dwg", WriteEmptyView);
		yield return Pair("g13_wide_polyline.dwg", WriteWidePolyline);
		yield return Pair("g13_long_text.dwg", WriteLongText);
		yield return Pair("g13_scale_1x.dwg", p => WriteScale(p, 1));
		yield return Pair("g13_scale_4x.dwg", p => WriteScale(p, 4));
		yield return Pair("g13_scale_16x.dwg", p => WriteScale(p, 16));
		yield return Pair("g13_many_inserts.dwg", WriteManyInserts);
		yield return Pair("g13_ac1009.dwg", WriteAc1009);
		yield return Pair("g13_dimension_deep.dwg", WriteDimensionDeep);
		yield return Pair("g13_dimension_shallow.dwg", WriteDimensionShallow);
		yield return Pair("g13_wide_spline.dwg", WriteWideSpline);
	}

	private static KeyValuePair<string, Action<string>> Pair(string name, Action<string> w)
	{
		return new KeyValuePair<string, Action<string>>(name, w);
	}

	// ------------------------------------------------------- one per kind

	public static void WriteLine(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Line(new XYZ(1.5, 2.25, 0), new XYZ(11.5, 22.25, 0)) { Layer = L(doc) });
		Write(doc, path);
	}

	public static void WritePolyline(string path)
	{
		CadDocument doc = NewDoc();

		LwPolyline lw = new LwPolyline { Layer = L(doc) };
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, 0)));
		// A bulge, which is an arc. It has to still be an arc on the other
		// side of the boundary: a shim that expanded it into segments would
		// have made the tessellation decision libviprs owns.
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 0)) { Bulge = 0.5 });
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 10)));
		doc.Entities.Add(lw);

		Polyline2D p2 = new Polyline2D();
		p2.Vertices.Add(new Vertex2D(new XY(20, 0)));
		p2.Vertices.Add(new Vertex2D(new XY(30, 5)));
		p2.Vertices.Add(new Vertex2D(new XY(40, 0)));
		doc.Entities.Add(p2);

		doc.Entities.Add(new Polyline3D(
			new List<XYZ> { new XYZ(50, 0, 0), new XYZ(60, 5, 5), new XYZ(70, 0, 10) },
			false));

		Write(doc, path);
	}

	public static void WriteArc(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Arc
		{
			Center = new XYZ(3.25, 4.5, 0),
			Radius = 7.125,
			StartAngle = 0.25,
			EndAngle = 2.75,
			Layer = L(doc),
		});
		Write(doc, path);
	}

	public static void WriteCircle(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Circle
		{
			Center = new XYZ(-5.5, 6.25, 0),
			Radius = 9.875,
			Layer = L(doc),
		});
		Write(doc, path);
	}

	public static void WriteEllipse(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Ellipse
		{
			Center = new XYZ(2.5, -3.5, 0),
			MajorAxisEndPoint = new XYZ(8.0, 0, 0),
			RadiusRatio = 0.375,
			StartParameter = 0.5,
			EndParameter = 4.25,
			Layer = L(doc),
		});
		Write(doc, path);
	}

	public static void WriteSpline(string path)
	{
		CadDocument doc = NewDoc();
		Spline s = new Spline { Degree = 3, Layer = L(doc) };
		s.ControlPoints.Add(new XYZ(0, 0, 0));
		s.ControlPoints.Add(new XYZ(5, 10, 0));
		s.ControlPoints.Add(new XYZ(15, -5, 0));
		s.ControlPoints.Add(new XYZ(20, 8, 0));
		foreach (double k in new double[] { 0, 0, 0, 0, 1, 1, 1, 1 })
		{
			s.Knots.Add(k);
		}

		doc.Entities.Add(s);
		Write(doc, path);
	}

	public static void WriteText(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new TextEntity
		{
			InsertPoint = new XYZ(1.25, 2.5, 0),
			Height = 3.75,
			Rotation = 0.125,
			Value = "VIPRS-G13-TEXT",
			Layer = L(doc),
		});
		doc.Entities.Add(new MText
		{
			InsertPoint = new XYZ(11.25, 12.5, 0),
			Height = 1.5,
			Value = "VIPRS-G13-MTEXT",
			Layer = L(doc),
		});
		Write(doc, path);
	}

	public static void WriteInsert(string path)
	{
		CadDocument doc = NewDoc();

		BlockRecord inner = new BlockRecord("VIPRS_G13_INNER");
		inner.Entities.Add(new Circle { Center = new XYZ(0, 0, 0), Radius = 1.5 });
		doc.BlockRecords.Add(inner);

		BlockRecord outer = new BlockRecord(BlockName);
		outer.Entities.Add(new Line(new XYZ(0, 0, 0), new XYZ(2, 0, 0)));
		outer.Entities.Add(new Insert(inner) { InsertPoint = new XYZ(3, 0, 0) });
		doc.BlockRecords.Add(outer);

		doc.Entities.Add(new Insert(outer)
		{
			InsertPoint = new XYZ(10, 20, 0),
			XScale = 2.0,
			YScale = 2.0,
			ZScale = 2.0,
			Rotation = 0.5,
			Layer = L(doc),
		});

		Write(doc, path);
	}

	public static void WriteDimension(string path)
	{
		CadDocument doc = NewDoc();
		DimensionLinear dim = new DimensionLinear
		{
			FirstPoint = new XYZ(0, 0, 0),
			SecondPoint = new XYZ(10, 0, 0),
			DefinitionPoint = new XYZ(10, 0, 0),
			TextMiddlePoint = new XYZ(5, 3, 0),
			Offset = 3,
			Layer = L(doc),
		};
		doc.Entities.Add(dim);
		dim.UpdateBlock();
		Write(doc, path);
	}

	public static void WriteHatch(string path)
	{
		CadDocument doc = NewDoc();

		// A loop of straight edges, which is exactly a closed polygon.
		Hatch square = new Hatch { Layer = L(doc), IsSolid = true };
		Hatch.BoundaryPath loop = new Hatch.BoundaryPath();
		loop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(0, 0), End = new XY(10, 0) });
		loop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(10, 0), End = new XY(10, 10) });
		loop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(10, 10), End = new XY(0, 10) });
		loop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(0, 10), End = new XY(0, 0) });
		square.Paths.Add(loop);
		doc.Entities.Add(square);

		// A loop carrying a circular arc. A bulge says that exactly, so this
		// one is one polygon with a bulge on the arc's span and no curve is
		// approximated anywhere.
		Hatch rounded = new Hatch { Layer = L(doc), IsSolid = true };
		Hatch.BoundaryPath arcLoop = new Hatch.BoundaryPath();
		arcLoop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(20, 0), End = new XY(30, 0) });
		arcLoop.Edges.Add(new Hatch.BoundaryPath.Arc
		{
			Center = new XY(30, 5),
			Radius = 5,
			StartAngle = -Math.PI / 2.0,
			EndAngle = Math.PI / 2.0,
			CounterClockWise = true,
		});
		arcLoop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(30, 10), End = new XY(20, 10) });
		arcLoop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(20, 10), End = new XY(20, 0) });
		rounded.Paths.Add(arcLoop);
		doc.Entities.Add(rounded);

		// The same shape with the arc traversed the other way round.
		//
		// A boundary arc's direction is a flag, not a sign on the sweep, and
		// upstream reads a clockwise edge as the arc from 2*pi - end to
		// 2*pi - start, so the edge's own first point is the entity's last. A
		// corpus with only counter-clockwise loops cannot tell a converter
		// that handles the flag from one that ignores it: both produce the
		// same bulge on every fixture there is. This one bulges inward, so
		// getting the sign wrong puts the arc outside the rectangle.
		Hatch clockwise = new Hatch { Layer = L(doc), IsSolid = true };
		Hatch.BoundaryPath cwLoop = new Hatch.BoundaryPath();
		cwLoop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(60, 0), End = new XY(70, 0) });
		cwLoop.Edges.Add(new Hatch.BoundaryPath.Arc
		{
			Center = new XY(70, 5),
			Radius = 5,
			StartAngle = Math.PI / 2.0,
			EndAngle = -Math.PI / 2.0,
			CounterClockWise = false,
		});
		cwLoop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(70, 10), End = new XY(60, 10) });
		cwLoop.Edges.Add(new Hatch.BoundaryPath.Line { Start = new XY(60, 10), End = new XY(60, 0) });
		clockwise.Paths.Add(cwLoop);
		doc.Entities.Add(clockwise);

		// A loop with a spline edge, which no closed polygon expresses. The
		// edges have to cross as themselves rather than as a polygon that
		// quietly straightened the spline.
		Hatch splined = new Hatch { Layer = L(doc), IsSolid = true };
		Hatch.BoundaryPath splineLoop = new Hatch.BoundaryPath();
		Hatch.BoundaryPath.Spline edge = new Hatch.BoundaryPath.Spline { Degree = 3 };
		edge.ControlPoints.Add(new XYZ(40, 0, 1));
		edge.ControlPoints.Add(new XYZ(45, 8, 1));
		edge.ControlPoints.Add(new XYZ(50, -4, 1));
		edge.ControlPoints.Add(new XYZ(55, 3, 1));
		foreach (double k in new double[] { 0, 0, 0, 0, 1, 1, 1, 1 })
		{
			edge.Knots.Add(k);
		}
		splineLoop.Edges.Add(edge);
		splined.Paths.Add(splineLoop);
		doc.Entities.Add(splined);

		// No boundary at all: the pattern is the only thing it carries.
		Hatch patternOnly = new Hatch { Layer = L(doc), IsSolid = false };
		doc.Entities.Add(patternOnly);

		Write(doc, path);
	}

	public static void WriteUnsupported(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Point { Location = new XYZ(1, 2, 3), Layer = L(doc) });
		doc.Entities.Add(new Solid { Layer = L(doc) });
		Write(doc, path);
	}

	// ------------------------------------------- the refused-entity corpus
	//
	// Every file below holds a kind the flattener refuses today, and every one
	// is shaped so that the plausible wrong implementation fails it. That is
	// the rule the g13_ocs_* files already state: a fixture built out of fixed
	// points passes whether the code is right or wrong, and is worse than no
	// fixture because it converts "untested" into "covered".
	//
	// Each one has two halves. The top-level entities produce records with
	// `flags` 0, and the block below produces records with bit 0 set, which
	// docs/WIRE.md defines as "came from expanding a nested insertion". A
	// fixture with only the first half lets through the defect that actually
	// ships: the kind is implemented, the top-level case works, the fixture is
	// green, and instances inside an INSERT are dropped or emitted
	// untransformed.

	// The second half every refused-kind fixture carries.
	//
	// Two insertions of one block, and neither is the identity. The first is
	// non-uniform, which is the only thing in these files that reaches the
	// flattener's NonUniform warning path. The second is mirrored, because a
	// mirror reverses what counter-clockwise means, and that is what decides a
	// mesh's face winding and a solid's corner order. A uniform upright insert
	// would exercise the flags bit and nothing else.
	private static void AddBlockInstances(CadDocument doc, string blockName, params Entity[] members)
	{
		BlockRecord block = new BlockRecord(blockName);
		foreach (Entity e in members)
		{
			block.Entities.Add(e);
		}
		doc.BlockRecords.Add(block);
		doc.Entities.Add(new Insert(block)
		{
			InsertPoint = new XYZ(40, 0, 0),
			XScale = 3.0,
			YScale = 1.5,
			ZScale = 1.0,
			Layer = L(doc),
		});
		doc.Entities.Add(new Insert(block)
		{
			InsertPoint = new XYZ(80, 0, 0),
			XScale = -2.0,
			YScale = 2.0,
			ZScale = 1.0,
			Layer = L(doc),
		});
	}

	// The extrusion the non-Z cases are built on. Not axis-aligned, and not a
	// unit vector as written, so an implementation that forgets to normalise
	// it is visible in the record rather than only in the geometry.
	private static readonly XYZ SkewNormal = new XYZ(1, 2, 2);

	// POINT, which is 40 of the 88 entities refused on real_AC1032.dwg.
	//
	// The fourth point is the one that earns the file. A point's location is in
	// the plane its normal defines, so an implementation that emits the stored
	// coordinate without the arbitrary-axis lift is right about the first three
	// and wrong about the last. The origin is here deliberately AND deliberately
	// not alone: it is a fixed point of every transform.
	public static void WritePoint(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Point { Location = new XYZ(0, 0, 0), Layer = L(doc) });
		doc.Entities.Add(new Point { Location = new XYZ(3.25, -7.5, 0), Layer = L(doc) });
		doc.Entities.Add(new Point { Location = new XYZ(-11.75, 4.5, 2.25), Layer = L(doc) });
		doc.Entities.Add(new Point
		{
			Location = new XYZ(2, 3, 5),
			Normal = SkewNormal,
			Layer = L(doc)
		});
		AddBlockInstances(doc, "VIPRS_G13_POINT_BLK",
			new Point { Location = new XYZ(1, 2, 0) },
			new Point { Location = new XYZ(2, 3, 5), Normal = SkewNormal });
		Write(doc, path);
	}

	// SOLID, and the corner order is the whole point.
	//
	// DXF stores the third and fourth corners swapped relative to traversal
	// order, so an implementation that emits 1,2,3,4 as a closed polygon draws a
	// bow-tie. Over a square that is invisible: a bow-tie and a correct quad
	// cover plausible areas, and any assertion on area or bounding box passes
	// either way. So the first solid has four corners with no symmetry at all,
	// and an expectation has to compare the emitted point ORDER.
	//
	// The second is the triangle case (fourth corner equal to the third). The
	// third carries a non-Z normal. The block copy is asymmetric too, because
	// the mirrored insertion is exactly where a corner-order defect and a
	// winding defect compound, and a symmetric quad would hide both at once.
	public static void WriteSolidQuad(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Solid
		{
			FirstCorner = new XYZ(0, 0, 0),
			SecondCorner = new XYZ(10, 1, 0),
			ThirdCorner = new XYZ(2, 5, 0),
			FourthCorner = new XYZ(11, 7, 0),
			Layer = L(doc)
		});
		doc.Entities.Add(new Solid
		{
			FirstCorner = new XYZ(20, 0, 0),
			SecondCorner = new XYZ(26, 2, 0),
			ThirdCorner = new XYZ(22, 6, 0),
			FourthCorner = new XYZ(22, 6, 0),
			Layer = L(doc)
		});
		doc.Entities.Add(new Solid
		{
			FirstCorner = new XYZ(0, 0, 0),
			SecondCorner = new XYZ(4, 0, 0),
			ThirdCorner = new XYZ(0, 3, 0),
			FourthCorner = new XYZ(4, 3, 0),
			Normal = SkewNormal,
			Layer = L(doc)
		});
		AddBlockInstances(doc, "VIPRS_G13_SOLID_BLK",
			new Solid
			{
				FirstCorner = new XYZ(0, 0, 0),
				SecondCorner = new XYZ(6, 1, 0),
				ThirdCorner = new XYZ(1, 4, 0),
				FourthCorner = new XYZ(7, 5, 0),
			});
		Write(doc, path);
	}

	// MLINE, which is a path plus a style and draws one line per style element.
	//
	// The style is the whole reason this file is more than one entity. An MLINE
	// holds a centre path and a handle to an MLINESTYLE, and what a drawing
	// shows is that path offset by each of the style's element offsets, scaled
	// by the entity's own ScaleFactor and measured along each vertex's miter.
	// So the fixture carries three styles and six MLINEs, and the questions it
	// is built to answer are which offsets, which reference, and along which
	// direction.
	//
	// Three styles: the plain one with three elements at +1, 0 and -1.5,
	// deliberately asymmetric so the top and bottom references are different
	// numbers and neither is zero; the same three offsets with FillOn and a
	// start cap, which is the only entity here that asks for something this
	// version does not draw; and one with no elements at all, which is the one
	// unresolvable state this layer can actually see. A dangling style HANDLE
	// is not producible: ACadSharp substitutes "Standard" at read time, so a
	// file whose style is missing decodes as though it said Standard.
	//
	// The first three MLINEs share one path and one scale factor and differ
	// only in Justification, because that is the comparison: an arm that
	// ignored Justification would emit the same three records three times and
	// every coordinate in them would still be real.
	//
	// The path bends at a right angle on purpose. Along a straight run the
	// miter and the segment perpendicular agree, so a straight fixture cannot
	// tell an arm that reads Vertex.Miter from one that does not.
	//
	// Every vertex also carries one Segment per element with the parameter
	// AutoCAD bakes into a real file, which is the distance along the miter to
	// that element's line. The adapter never reads them. They are here because
	// the DWG writer needs one segment per element per vertex to write the
	// entity at all, and because a file that carries the wrong ones is not the
	// file AutoCAD would have written.
	private const double MLineScale = 2.5;

	private static readonly double[] MLineOffsets = new double[] { 1.0, 0.0, -1.5 };

	private static XYZ MLineUnit(XYZ v)
	{
		double n = Math.Sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z);
		return n == 0.0 ? v : new XYZ(v.X / n, v.Y / n, v.Z / n);
	}

	private static XYZ MLineSide(XYZ direction)
	{
		// The left of the direction about +Z, which is the side a positive
		// offset lies on. Confirmed against real_AC1032.dwg's three MLINEs,
		// which AutoCAD wrote, through both ezdxf's virtual_entities and the
		// parameters baked into the file.
		XYZ d = MLineUnit(direction);
		return MLineUnit(new XYZ(-d.Y, d.X, 0));
	}

	// One vertex of an MLINE, with the miter worked out from the segments
	// either side of it and one segment per element carrying its parameter.
	//
	// `incoming` is null at the start of an open path and `outgoing` is null at
	// its end, which is where the miter is the plain perpendicular rather than
	// a bisector.
	private static MLine.Vertex MLineVertex(
		XYZ position,
		XYZ? incoming,
		XYZ? outgoing,
		double reference,
		double scale
	)
	{
		XYZ direction = outgoing.HasValue ? outgoing.Value : incoming.Value;
		XYZ miter;
		if (!outgoing.HasValue)
		{
			miter = MLineSide(incoming.Value);
		}
		else if (!incoming.HasValue)
		{
			miter = MLineSide(outgoing.Value);
		}
		else
		{
			XYZ a = MLineSide(incoming.Value);
			XYZ b = MLineSide(outgoing.Value);
			miter = MLineUnit(new XYZ(a.X + b.X, a.Y + b.Y, a.Z + b.Z));
		}

		XYZ side = MLineSide(direction);
		double denom = miter.X * side.X + miter.Y * side.Y + miter.Z * side.Z;

		MLine.Vertex v = new MLine.Vertex
		{
			Position = position,
			Direction = MLineUnit(direction),
			Miter = miter
		};
		foreach (double offset in MLineOffsets)
		{
			MLine.Vertex.Segment segment = new MLine.Vertex.Segment();
			segment.Parameters.Add((offset - reference) * scale / denom);
			segment.Parameters.Add(0.0);
			v.Segments.Add(segment);
		}

		return v;
	}

	private static MLine NewMLine(
		MLineStyle style,
		MLineJustification justification,
		double scale,
		bool closed,
		params XYZ[] path
	)
	{
		MLine mline = NewEmptyMLine(style, justification, scale, closed, path[0]);
		FillMLinePath(mline, justification, scale, closed, path);
		return mline;
	}

	private static MLine NewEmptyMLine(
		MLineStyle style,
		MLineJustification justification,
		double scale,
		bool closed,
		XYZ start
	)
	{
		return new MLine
		{
			Style = style,
			Justification = justification,
			ScaleFactor = scale,
			StartPoint = start,
			Flags = closed ? MLineFlags.Has | MLineFlags.Closed : MLineFlags.Has
		};
	}

	// The vertices, separately from the entity, because of an upstream defect
	// that a block fixture walks straight into.
	//
	// ACadSharp 3.7.1's MLine.Clone calls base.Clone (a MemberwiseClone, so the
	// clone's Vertices is the SAME List object as the original's), then clears
	// it and refills it from this.Vertices, which by then is the list it just
	// emptied. Both copies come out with no vertices. `new Insert(block)` clones
	// the block record when the record already belongs to a document, and
	// AddBlockInstances makes two of them, so an MLINE placed in a block before
	// the insertions exist is silently emptied and writes as an entity with
	// nothing in it. Filling the path after the insertions are made is the whole
	// of the workaround; nothing in the adapter is involved.
	private static void FillMLinePath(
		MLine mline,
		MLineJustification justification,
		double scale,
		bool closed,
		params XYZ[] path
	)
	{
		double reference = 0.0;
		if (justification == MLineJustification.Top)
		{
			reference = MLineOffsets[0];
			foreach (double o in MLineOffsets)
			{
				reference = Math.Max(reference, o);
			}
		}
		else if (justification == MLineJustification.Bottom)
		{
			reference = MLineOffsets[0];
			foreach (double o in MLineOffsets)
			{
				reference = Math.Min(reference, o);
			}
		}

		int n = path.Length;
		for (int i = 0; i < n; i++)
		{
			XYZ? incoming = null;
			XYZ? outgoing = null;
			if (i > 0)
			{
				incoming = path[i] - path[i - 1];
			}
			else if (closed)
			{
				incoming = path[0] - path[n - 1];
			}

			if (i < n - 1)
			{
				outgoing = path[i + 1] - path[i];
			}
			else if (closed)
			{
				outgoing = path[0] - path[n - 1];
			}

			mline.Vertices.Add(MLineVertex(path[i], incoming, outgoing, reference, scale));
		}
	}

	private static MLineStyle NewMLineStyle(string name, MLineStyleFlags flags, bool withElements)
	{
		MLineStyle style = new MLineStyle(name) { Flags = flags };
		if (withElements)
		{
			foreach (double offset in MLineOffsets)
			{
				style.AddElement(new MLineStyle.Element { Offset = offset });
			}
		}

		return style;
	}

	public static void WriteMLine(string path)
	{
		CadDocument doc = NewDoc();
		MLineStyle plain = NewMLineStyle("VIPRS_G13_MLS", MLineStyleFlags.None, true);
		MLineStyle caps = NewMLineStyle(
			"VIPRS_G13_MLS_CAPS",
			MLineStyleFlags.FillOn | MLineStyleFlags.StartSquareCap,
			true
		);
		MLineStyle empty = NewMLineStyle("VIPRS_G13_MLS_EMPTY", MLineStyleFlags.None, false);
		doc.MLineStyles.Add(plain);
		doc.MLineStyles.Add(caps);
		doc.MLineStyles.Add(empty);

		XYZ[] bend = new XYZ[] { new XYZ(0, 0, 0), new XYZ(10, 0, 0), new XYZ(10, 8, 0) };
		foreach (MLineJustification j in new MLineJustification[]
		{
			MLineJustification.Zero,
			MLineJustification.Top,
			MLineJustification.Bottom
		})
		{
			MLine m = NewMLine(plain, j, MLineScale, false, bend);
			m.Layer = L(doc);
			doc.Entities.Add(m);
		}

		MLine square = NewMLine(
			plain,
			MLineJustification.Zero,
			MLineScale,
			true,
			new XYZ(30, 0, 0),
			new XYZ(40, 0, 0),
			new XYZ(40, 10, 0),
			new XYZ(30, 10, 0)
		);
		square.Layer = L(doc);
		doc.Entities.Add(square);

		MLine nothingToPlace = NewMLine(
			empty,
			MLineJustification.Zero,
			MLineScale,
			false,
			new XYZ(50, 0, 0),
			new XYZ(58, 0, 0)
		);
		foreach (MLine.Vertex v in nothingToPlace.Vertices)
		{
			v.Segments.Clear();
		}
		nothingToPlace.Layer = L(doc);
		doc.Entities.Add(nothingToPlace);

		MLine asksForMore = NewMLine(
			caps,
			MLineJustification.Zero,
			MLineScale,
			false,
			new XYZ(60, 0, 0),
			new XYZ(70, 0, 0)
		);
		asksForMore.Layer = L(doc);
		doc.Entities.Add(asksForMore);

		// The block half runs on a slant, because three horizontal lines are
		// parallel whatever an arm does with them and the assertion under the
		// two insertions is that they stay parallel.
		MLine inBlock = NewEmptyMLine(
			plain,
			MLineJustification.Zero,
			1.0,
			false,
			new XYZ(0, 0, 0)
		);
		AddBlockInstances(doc, "VIPRS_G13_MLINE_BLK", inBlock);
		FillMLinePath(
			inBlock,
			MLineJustification.Zero,
			1.0,
			false,
			new XYZ(0, 0, 0),
			new XYZ(6, 3, 0)
		);
		Write(doc, path);
	}

	// RAY and XLINE, the two unbounded kinds, plus a bounded line.
	//
	// The line is load-bearing rather than decorative: it gives the drawing
	// finite extents, so an implementation that decides to clip against them
	// produces a specific wrong answer a test can pin instead of an unbounded
	// one that errors and tells nobody anything.
	//
	// Neither direction is axis-aligned, for the same reason: an axis-aligned
	// construction line clipped against an axis-aligned extents box lands on the
	// box corners, which are fixed points, so a clipper that transposed X and Y
	// would still look right. The mirrored insertion matters more here than
	// anywhere else in this corpus, because mirroring a RAY reverses the half
	// line it covers, and a ray pointing the wrong way is still a ray.
	public static void WriteRayXline(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Line(new XYZ(-10, -10, 0), new XYZ(10, 10, 0)) { Layer = L(doc) });
		doc.Entities.Add(new Ray
		{
			StartPoint = new XYZ(1, 2, 0),
			Direction = new XYZ(3, 1, 0),
			Layer = L(doc)
		});
		doc.Entities.Add(new XLine
		{
			FirstPoint = new XYZ(-2, 3, 0),
			Direction = new XYZ(1, 4, 0),
			Layer = L(doc)
		});
		AddBlockInstances(doc, "VIPRS_G13_RAY_BLK",
			new Ray { StartPoint = new XYZ(0, 1, 0), Direction = new XYZ(2, 1, 0) },
			new XLine { FirstPoint = new XYZ(0, -1, 0), Direction = new XYZ(1, 3, 0) });
		Write(doc, path);
	}

	// POLYFACE_MESH, which the flattener emits as a Polyline today because
	// PolyfaceMesh derives from Polyline<T> and matches the `case IPolyline` arm.
	//
	// The vertex ORDER is what makes this file worth having. If the storage
	// order happened to be a sensible path, the wrong output and the right
	// output would look similar and the fixture would prove nothing. These six
	// are ordered so a line threaded through them in storage order
	// self-intersects and leaves the surface, which no correct rendering of two
	// faces does.
	public static void WritePolyfaceMesh(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(NewPolyfaceMesh(L(doc), XYZ.AxisZ));
		doc.Entities.Add(NewPolyfaceMesh(L(doc), SkewNormal));
		AddBlockInstances(doc, "VIPRS_G13_PFACE_BLK", NewPolyfaceMesh(null, XYZ.AxisZ));
		Write(doc, path);
	}

	private static PolyfaceMesh NewPolyfaceMesh(Layer layer, XYZ normal)
	{
		PolyfaceMesh mesh = new PolyfaceMesh { Normal = normal };
		if (layer != null)
		{
			mesh.Layer = layer;
		}
		mesh.Vertices.Add(new VertexFaceMesh { Location = new XYZ(0, 0, 0) });
		mesh.Vertices.Add(new VertexFaceMesh { Location = new XYZ(10, 0, 0) });
		mesh.Vertices.Add(new VertexFaceMesh { Location = new XYZ(0, 10, 0) });
		mesh.Vertices.Add(new VertexFaceMesh { Location = new XYZ(10, 10, 6) });
		mesh.Vertices.Add(new VertexFaceMesh { Location = new XYZ(20, 0, 6) });
		mesh.Vertices.Add(new VertexFaceMesh { Location = new XYZ(20, 10, 0) });
		mesh.Faces.Add(new VertexFaceRecord { Index1 = 1, Index2 = 2, Index3 = 4, Index4 = 3 });
		mesh.Faces.Add(new VertexFaceRecord { Index1 = 2, Index2 = 5, Index3 = 6, Index4 = 4 });
		return mesh;
	}

	// POLYGON_MESH, the other kind the IPolyline arm catches.
	//
	// A 3x4 grid rather than a square one, so an implementation that transposes
	// M and N is caught by the record's own shape rather than by someone looking
	// at a picture. The alternating elevation keeps it off a single plane.
	public static void WritePolygonMesh(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(NewPolygonMesh(L(doc), XYZ.AxisZ));
		doc.Entities.Add(NewPolygonMesh(L(doc), SkewNormal));
		AddBlockInstances(doc, "VIPRS_G13_PMESH_BLK", NewPolygonMesh(null, XYZ.AxisZ));
		Write(doc, path);
	}

	private static PolygonMesh NewPolygonMesh(Layer layer, XYZ normal)
	{
		PolygonMesh mesh = new PolygonMesh { MVertexCount = 3, NVertexCount = 4, Normal = normal };
		if (layer != null)
		{
			mesh.Layer = layer;
		}
		for (int m = 0; m < 3; m++)
		{
			for (int n = 0; n < 4; n++)
			{
				mesh.Vertices.Add(new PolygonMeshVertex
				{
					Location = new XYZ(m * 5.0, n * 3.0, (m + n) % 2 == 0 ? 0.0 : 1.5)
				});
			}
		}
		return mesh;
	}

	// MESH: an explicit vertex list and face indices, no proprietary format
	// anywhere, which is what separates it from 3DSOLID and REGION.
	//
	// The faces are deliberately not coplanar, because coplanar faces let an
	// implementation that collapses the mesh to one polygon look correct. The
	// subdivision level is deliberately non-zero for the same class of reason:
	// the proposal is to emit the base mesh and ignore the level, and a fixture
	// at level zero cannot show that anything was ignored.
	//
	// The mirrored insertion is what makes face winding testable: a mirror
	// reverses it, and a mesh whose faces face the wrong way renders inside out
	// rather than obviously wrong.
	public static void WriteMesh(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(NewMesh(L(doc)));
		AddBlockInstances(doc, "VIPRS_G13_MESH_BLK", NewMesh(null));
		Write(doc, path);
	}

	private static Mesh NewMesh(Layer layer)
	{
		Mesh mesh = new Mesh { SubdivisionLevel = 2 };
		if (layer != null)
		{
			mesh.Layer = layer;
		}
		mesh.Vertices.Add(new XYZ(0, 0, 0));
		mesh.Vertices.Add(new XYZ(10, 0, 0));
		mesh.Vertices.Add(new XYZ(10, 10, 0));
		mesh.Vertices.Add(new XYZ(0, 10, 4));
		mesh.Faces.Add(new int[] { 0, 1, 2 });
		mesh.Faces.Add(new int[] { 0, 2, 3 });
		return mesh;
	}

	// A MESH whose face list contradicts the vertex list beside it.
	//
	// Warning 111, MESH_FACE_UNREADABLE, is the only hostile-input handling in
	// the flattener's own C#: everything else malformed is refused at the door
	// by the reader or by a bound. It was declared, documented and reachable
	// with nothing in the corpus producing it, so both branches of Unreadable
	// and the sentence they build had never run on a real file.
	//
	// A file controls the face list and the vertex list independently, so
	// every fault below is one a drawing can actually carry, and all four are
	// here because they are two different branches and two different ends of
	// each: an index past the end, an index below zero, a face of two vertices
	// and a face of none. The negative one is the half a bounds check written
	// as `>= vertices.Count` misses entirely, and it fails as an array index
	// rather than as a warning.
	//
	// The good face is first and it is the point of the file rather than
	// decoration. The claim behind code 111 is that one entity's worth of bad
	// data drops a face and lets the rest of the mesh cross, and a file of
	// nothing but bad faces cannot tell that apart from a decoder that gave up
	// quietly. It is deliberately off the +Z plane too, so a normal that was
	// fabricated rather than measured off the face shows up in the record.
	//
	// One entity, no block, and subdivision level 0: the interesting thing
	// here is the face list, and a second copy of it under an insertion would
	// only double every warning.
	public static void WriteMeshBadFaces(string path)
	{
		CadDocument doc = NewDoc();
		Mesh mesh = new Mesh { Layer = L(doc) };
		mesh.Vertices.Add(new XYZ(0, 0, 0));
		mesh.Vertices.Add(new XYZ(10, 0, 0));
		mesh.Vertices.Add(new XYZ(10, 10, 4));
		mesh.Faces.Add(new int[] { 0, 1, 2 });
		mesh.Faces.Add(new int[] { 0, 1, 99 });
		mesh.Faces.Add(new int[] { 0, 1 });
		mesh.Faces.Add(new int[] { 0, 1, -1 });
		mesh.Faces.Add(new int[] { });
		doc.Entities.Add(mesh);
		Write(doc, path);
	}

	// TOLERANCE: a feature-control frame, whose geometry is computed from the
	// dimension style rather than read out of the file, which makes it the
	// refused kind most likely to disagree with what AutoCAD draws.
	//
	// Two stacked rows, because a single-row frame does not exercise the
	// vertical stacking that is the part most likely to be wrong. The non-Z
	// copy is here for the same reason the other files carry one: at +Z the
	// arbitrary axis algorithm is exactly the identity.
	public static void WriteTolerance(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(NewTolerance(L(doc), XYZ.AxisZ));
		doc.Entities.Add(NewTolerance(L(doc), SkewNormal));
		AddBlockInstances(doc, "VIPRS_G13_TOL_BLK", NewTolerance(null, XYZ.AxisZ));
		Write(doc, path);
	}

	private static Tolerance NewTolerance(Layer layer, XYZ normal)
	{
		Tolerance tol = new Tolerance
		{
			InsertionPoint = new XYZ(4, 6, 0),
			Direction = new XYZ(1, 0, 0),
			Normal = normal,
			Text = "{\\Fgdt;j}%%v{\\Fgdt;n}0.25{\\Fgdt;m}%%vA%%v%%v%%v\\P{\\Fgdt;b}%%v0.5%%vB",
		};
		if (layer != null)
		{
			tol.Layer = layer;
		}
		return tol;
	}

	public static void WriteXref(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord xref = new BlockRecord(XrefBlockName, XrefPath);
		doc.BlockRecords.Add(xref);
		doc.Entities.Add(new Insert(xref) { InsertPoint = new XYZ(5, 5, 0), Layer = L(doc) });
		Write(doc, path);
	}

	// The same drawing with an 8192-byte reference path. Nothing here is
	// unusual except the length: the warning the decoder writes about it
	// carries the file's own string, and a bound below that length used to end
	// the whole decode rather than shorten one message.
	public static void WriteXrefLong(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord xref = new BlockRecord(XrefBlockName, LongXrefPath());
		doc.BlockRecords.Add(xref);
		doc.Entities.Add(new Insert(xref) { InsertPoint = new XYZ(5, 5, 0), Layer = L(doc) });
		Write(doc, path);
	}

	public static void WriteNonUniform(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord block = new BlockRecord("VIPRS_G13_SQUASH");
		block.Entities.Add(new Circle { Center = new XYZ(0, 0, 0), Radius = 4.0 });
		doc.BlockRecords.Add(block);
		doc.Entities.Add(new Insert(block)
		{
			InsertPoint = new XYZ(0, 0, 0),
			XScale = 3.0,
			YScale = 1.0,
			ZScale = 1.0,
			Layer = L(doc),
		});
		Write(doc, path);
	}

	public static void WriteTwoEntities(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Line(new XYZ(0, 0, 0), new XYZ(1, 1, 0)) { Layer = L(doc) });
		doc.Entities.Add(new Line(new XYZ(2, 2, 0), new XYZ(3, 3, 0)) { Layer = L(doc) });
		Write(doc, path);
	}

	public static void WriteDeepBlocks(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord deepest = new BlockRecord("VIPRS_G13_DEPTH_0");
		deepest.Entities.Add(new Line(new XYZ(0, 0, 0), new XYZ(1, 0, 0)));
		doc.BlockRecords.Add(deepest);

		BlockRecord current = deepest;
		for (int i = 1; i < DeepBlockDepth; i++)
		{
			BlockRecord next = new BlockRecord("VIPRS_G13_DEPTH_" + i);
			next.Entities.Add(new Insert(current) { InsertPoint = new XYZ(i, 0, 0) });
			doc.BlockRecords.Add(next);
			current = next;
		}

		doc.Entities.Add(new Insert(current) { InsertPoint = new XYZ(0, 0, 0), Layer = L(doc) });
		Write(doc, path);
	}

	// ------------------------------------------------------- bulge shapes

	// A closed slot: two straight sides and two semicircular ends, as one
	// LwPolyline with bulges [0, 1, 0, 1].
	//
	// Three things about it are not in any other fixture. It is closed, and
	// the closing span carries a bulge, so a producer that forgets either
	// draws a different shape. A bulge of exactly 1 is a half turn, which is
	// the largest sweep a single span can carry and the one place a
	// centre-and-angles form has to pick a side. And the shape is convex and
	// symmetric, so a sign error is visible as a bow tie rather than as a
	// small numerical difference.
	public const double SlotLength = 20.0;
	public const double SlotWidth = 10.0;

	private static LwPolyline Slot()
	{
		LwPolyline lw = new LwPolyline { IsClosed = true };
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, 0)));
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(SlotLength, 0)) { Bulge = 1.0 });
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(SlotLength, SlotWidth)));
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, SlotWidth)) { Bulge = 1.0 });
		return lw;
	}

	public static void WriteSlot(string path)
	{
		CadDocument doc = NewDoc();
		LwPolyline lw = Slot();
		lw.Layer = L(doc);
		doc.Entities.Add(lw);
		Write(doc, path);
	}

	// The same slot, in a block, inserted three times.
	//
	// Under block expansion every instance emits the block entity's own
	// handle, so three instances are three groups of records carrying one
	// handle with nothing between them. Grouping by handle merges the three
	// into one path; grouping by contiguity cannot find the seams. One record
	// per instance is what makes the question go away.
	public const int SlotInstanceCount = 3;

	public static void WriteSlotBlock(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord block = new BlockRecord("VIPRS_G13_SLOT");
		block.Entities.Add(Slot());
		doc.BlockRecords.Add(block);

		Layer layer = L(doc);
		for (int i = 0; i < SlotInstanceCount; i++)
		{
			doc.Entities.Add(new Insert(block)
			{
				InsertPoint = new XYZ(0, i * 30.0, 0),
				Layer = layer,
			});
		}

		Write(doc, path);
	}

	// A bulged polyline inside a block inserted with XScale -1.
	//
	// A reflection flips which side of the chord an arc bulges to, and the
	// bulge's sign does not follow the points on its own. The two scale
	// magnitudes are equal, so nothing here is a non-uniform scale: the only
	// thing this transform does that a rotation cannot is change handedness.
	public const double MirroredBulge = 0.5;

	public static void WriteMirroredBulge(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord block = new BlockRecord("VIPRS_G13_MIRROR");
		LwPolyline lw = new LwPolyline();
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, 0)));
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 0)) { Bulge = MirroredBulge });
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 10)));
		block.Entities.Add(lw);
		doc.BlockRecords.Add(block);

		doc.Entities.Add(new Insert(block)
		{
			InsertPoint = new XYZ(0, 0, 0),
			XScale = -1.0,
			YScale = 1.0,
			ZScale = 1.0,
			Layer = L(doc),
		});

		Write(doc, path);
	}

	// ------------------------------------------------- object coordinates

	// An entity's coordinates are in the object coordinate system its
	// extrusion direction defines, and the arbitrary-axis algorithm is what
	// turns one into a world coordinate. Every fixture above this point has an
	// extrusion of +Z, where that algorithm is the identity, so a shim that
	// never ran it produced exactly the right answer on the whole corpus.
	//
	// These four are the ones where it is not the identity.

	// The extrusion the whole OCS set is built on.
	//
	// -Z is the one a real drawing produces constantly: mirroring an entity in
	// AutoCAD flips the extrusion rather than the geometry, so half the arcs
	// and circles in a drawing somebody has edited carry it. Its arbitrary
	// axis is x = (-1, 0, 0), y = (0, 1, 0), so the lift negates x and z and
	// leaves y alone: a probe with x = 0 is a fixed point and says nothing.
	public static readonly XYZ OcsFlipped = new XYZ(0, 0, -1);

	// And an extrusion that is not on an axis at all, so the lift is a general
	// rotation rather than a pair of sign flips. Deliberately not a unit
	// vector in the file: a drawing is allowed to store one that is not, and
	// the algorithm normalises.
	public static readonly XYZ OcsOblique = new XYZ(1, 2, 2);

	// The OCS z of the oblique circle, which is what makes it a probe. A point
	// at OCS z = k lands on the plane { p : p . N = k }, whatever frame the
	// algorithm picks inside that plane, so the test can pin the lift without
	// reimplementing the choice of x axis.
	public const double OcsObliqueElevation = 7.0;

	public static void WriteOcsPlane(string path)
	{
		CadDocument doc = NewDoc();

		// Centre (4, 3, 0) in the entity's own plane, which is (-4, 3, 0) in
		// the world. x moves and y does not, so the x is the probe.
		doc.Entities.Add(new Arc
		{
			Center = new XYZ(4, 3, 0),
			Radius = 2.0,
			StartAngle = 0.0,
			EndAngle = Math.PI / 2.0,
			Normal = OcsFlipped,
			Layer = L(doc),
		});

		// A non-zero OCS z as well, so the fixture covers the third column of
		// the lift and not only the two in the plane.
		doc.Entities.Add(new Circle
		{
			Center = new XYZ(6, -2, 1),
			Radius = 3.0,
			Normal = OcsFlipped,
			Layer = L(doc),
		});

		// A bulged LWPOLYLINE in the same plane. Its elevation is the OCS z of
		// every vertex, which is the part a shim that lifted only the x and y
		// of a vertex would drop.
		LwPolyline lw = new LwPolyline { Normal = OcsFlipped, Elevation = 2.0, Layer = L(doc) };
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, 0)));
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 0)) { Bulge = MirroredBulge });
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 10)));
		doc.Entities.Add(lw);

		// The oblique one, whose lift is a rotation no pair of sign flips
		// reproduces.
		doc.Entities.Add(new Circle
		{
			Center = new XYZ(5, 0, OcsObliqueElevation),
			Radius = 1.0,
			Normal = OcsOblique,
			Layer = L(doc),
		});

		// A 2D POLYLINE in the same plane, which is the other half of the
		// IPolyline arm. An LWPOLYLINE and a POLYLINE reach the flattener
		// through different cases, and this is the one that shares its case
		// with the 3D polyline below, so without it the lift on that arm is
		// never run at all.
		Polyline2D p2 = new Polyline2D { Normal = OcsFlipped, Elevation = 2.0, Layer = L(doc) };
		p2.Vertices.Add(new Vertex2D(new XY(20, 0)));
		p2.Vertices.Add(new Vertex2D(new XY(30, 5)));
		p2.Vertices.Add(new Vertex2D(new XY(40, 0)));
		doc.Entities.Add(p2);

		// The control, and the reason this is not "lift every polyline".
		// POLYLINE's 3D flag is exactly the flag that says its vertices are
		// world coordinates, so lifting these three would move points that are
		// already where they belong. The extrusion is still -Z, so a lift
		// applied here would be visible rather than silently the identity.
		doc.Entities.Add(new Polyline3D(
			new List<XYZ> { new XYZ(1, 2, 3), new XYZ(4, 5, 6), new XYZ(7, 8, 9) },
			false)
		{
			Normal = OcsFlipped,
			Layer = L(doc),
		});

		Write(doc, path);
	}

	// The arc, circle and ellipse of a mirrored insertion, with the same block
	// inserted unmirrored beside them as the control.
	//
	// g13_mirrored_bulge.dwg pins the polyline half of this. An ARC's
	// start_angle and end_angle are counter-clockwise about its normal and a
	// reflection flips what counter-clockwise means, so an arc that follows
	// nothing is drawn as a different arc; an ELLIPSE's two parameters are the
	// same statement. A CIRCLE has neither and is the control inside the
	// control: it has to come out as the plain mirror image.
	//
	// The two inserts share the block, so the mirrored records are the
	// unmirrored ones with x negated and nothing else. That relation is what
	// the test asserts, which means the fixture carries its own expected
	// values rather than the test carrying a number somebody typed.
	public const string OcsMirrorBlockName = "VIPRS_G13_OCS_MIRROR";

	public static void WriteOcsMirror(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord block = new BlockRecord(OcsMirrorBlockName);

		// A quarter turn starting on the x axis. Its midpoint is at 45
		// degrees, off both axes, so the reflection moves it: an arc from 0 to
		// pi would have its midpoint at (10, 4) on the mirror's own axis and
		// could not tell a correct answer from the wrong one.
		block.Entities.Add(new Arc
		{
			Center = new XYZ(10, 0, 0),
			Radius = 4.0,
			StartAngle = 0.0,
			EndAngle = Math.PI / 2.0,
		});

		block.Entities.Add(new Circle { Center = new XYZ(10, 20, 0), Radius = 3.0 });

		block.Entities.Add(new Ellipse
		{
			Center = new XYZ(10, 40, 0),
			MajorAxisEndPoint = new XYZ(5, 0, 0),
			RadiusRatio = 0.5,
			StartParameter = 0.0,
			EndParameter = Math.PI / 2.0,
		});

		doc.BlockRecords.Add(block);

		doc.Entities.Add(new Insert(block)
		{
			InsertPoint = new XYZ(0, 0, 0),
			XScale = 1.0,
			YScale = 1.0,
			ZScale = 1.0,
			Layer = L(doc),
		});

		doc.Entities.Add(new Insert(block)
		{
			InsertPoint = new XYZ(0, 0, 0),
			XScale = -1.0,
			YScale = 1.0,
			ZScale = 1.0,
			Layer = L(doc),
		});

		Write(doc, path);
	}

	// An insertion whose own extrusion is +Y, which takes the block's XY plane
	// to the world's XZ plane.
	//
	// Every record here carries a normal, and until this fixture existed the
	// shim copied the entity's own normal onto the wire and never transformed
	// it. Under a transform that stays inside the XY plane that is invisible,
	// because +Z goes to +Z. Here it is a plane at right angles to the one the
	// record claims, and a bulge measured about the wrong normal puts the arc
	// somewhere the drawing does not have one.
	public static readonly XYZ OcsRotatedExtrusion = new XYZ(0, 1, 0);

	public static void WriteOcsRotated(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord block = new BlockRecord("VIPRS_G13_OCS_ROTATED");

		block.Entities.Add(new Circle { Center = new XYZ(3, 0, 0), Radius = 2.0 });
		block.Entities.Add(new Arc
		{
			Center = new XYZ(0, 0, 0),
			Radius = 5.0,
			StartAngle = 0.0,
			EndAngle = Math.PI / 2.0,
		});

		LwPolyline lw = new LwPolyline();
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, 0)));
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 0)) { Bulge = MirroredBulge });
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 10)));
		block.Entities.Add(lw);

		doc.BlockRecords.Add(block);

		doc.Entities.Add(new Insert(block)
		{
			InsertPoint = new XYZ(0, 0, 0),
			Normal = OcsRotatedExtrusion,
			XScale = 1.0,
			YScale = 1.0,
			ZScale = 1.0,
			Layer = L(doc),
		});

		Write(doc, path);
	}

	// A uniform scale composed with a rotation, which is not a uniform scale.
	//
	// The outer insertion scales x by three and the inner one is turned
	// forty-five degrees inside it, so the two basis vectors come out the same
	// length and stop being at right angles to each other. A circle under this
	// is an ellipse. The gate that decides whether NON_UNIFORM_BLOCK_SCALE is
	// raised compared the two lengths and nothing else, so it called this
	// uniform and said nothing, which is the one direction that does damage:
	// a consumer is told the parameters describe the shape.
	public static void WriteOcsSkew(string path)
	{
		CadDocument doc = NewDoc();

		BlockRecord inner = new BlockRecord("VIPRS_G13_OCS_SKEW_INNER");
		inner.Entities.Add(new Circle { Center = new XYZ(0, 0, 0), Radius = 4.0 });
		LwPolyline lw = new LwPolyline();
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, 0)));
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 0)) { Bulge = MirroredBulge });
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 10)));
		inner.Entities.Add(lw);
		doc.BlockRecords.Add(inner);

		BlockRecord outer = new BlockRecord("VIPRS_G13_OCS_SKEW");
		outer.Entities.Add(new Insert(inner)
		{
			InsertPoint = new XYZ(0, 0, 0),
			Rotation = Math.PI / 4.0,
		});
		doc.BlockRecords.Add(outer);

		doc.Entities.Add(new Insert(outer)
		{
			InsertPoint = new XYZ(0, 0, 0),
			XScale = 3.0,
			YScale = 1.0,
			ZScale = 1.0,
			Layer = L(doc),
		});

		Write(doc, path);
	}

	// ------------------------------------------------------- view extents

	// A drawing whose view extents are not numbers.
	//
	// docs/WIRE.md's finiteness guarantee covers records 3 to 10 and stops
	// short of ViewBegin, on the grounds that its extents are a bounding box
	// the source reports rather than a shape anybody draws, and a view holding
	// nothing has no finite one. That is defensible and it is also a hole: a
	// consumer reading min_x as NaN gets exactly the failure the guarantee
	// exists to prevent, and a bounding box that is NaN in one direction is NaN
	// in every direction by the time anything has compared it.
	//
	// A layout's extents are four doubles a file holds, so all three shapes are
	// reachable, and this fixture carries a NaN and both infinities in one view.
	// The line is there so the view is not empty: "no extents" and "nothing to
	// have extents of" are the two cases the record has to be able to tell
	// apart, and this is the first.
	public static void WriteBadExtents(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new Line(new XYZ(0, 0, 0), new XYZ(10, 5, 0)) { Layer = L(doc) });

		foreach (Layout l in doc.Layouts)
		{
			l.MinExtents = new XYZ(double.NaN, double.NegativeInfinity, 0.0);
			l.MaxExtents = new XYZ(double.PositiveInfinity, double.NaN, 0.0);
		}

		Write(doc, path);
	}

	// A drawing with nothing in it.
	//
	// The corpus had no such file, and it could not have had one by accident:
	// WriteBadExtents above adds a line on purpose so its view is not empty,
	// because "no usable extents" and "nothing to have extents of" are two
	// different statements and that fixture is the first. This is the second.
	//
	// Everything else here is the default document, which is the point. Model
	// space holds no entity, the reader still has things to say about the file
	// it read, and those notifications head every view's stream, so the stream
	// this produces is warnings and no geometry. A producer that asked whether
	// the view emitted any record at all would look at this one and decide it
	// was fine.
	public static void WriteEmptyView(string path)
	{
		CadDocument doc = NewDoc();
		Write(doc, path);
	}

	// A polyline carrying a bulge that is not a number.
	//
	// Nothing in a drawing has to be finite. A file is bytes somebody else
	// wrote, and an IEEE-754 double has 2^53 bit patterns that are NaN and two
	// that are infinite; a producer that hands one of those to a consumer has
	// put a value on the wire that no arithmetic on the other side recovers
	// from. The corpus had no file that carried one.
	public static void WriteNanBulge(string path)
	{
		CadDocument doc = NewDoc();
		LwPolyline lw = new LwPolyline { Layer = L(doc) };
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(0, 0)));
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 0)) { Bulge = double.NaN });
		lw.Vertices.Add(new LwPolyline.Vertex(new XY(10, 10)));
		doc.Entities.Add(lw);

		// A second polyline with an infinite coordinate, because a NaN and an
		// infinity fail differently: a comparison against NaN is false whichever
		// way it is written, and an infinity compares fine and then propagates.
		LwPolyline inf = new LwPolyline { Layer = L(doc) };
		inf.Vertices.Add(new LwPolyline.Vertex(new XY(20, 0)));
		inf.Vertices.Add(new LwPolyline.Vertex(new XY(double.PositiveInfinity, 0)));
		inf.Vertices.Add(new LwPolyline.Vertex(new XY(30, 10)));
		doc.Entities.Add(inf);

		Write(doc, path);
	}

	public static void WriteWidePolyline(string path)
	{
		CadDocument doc = NewDoc();
		LwPolyline lw = new LwPolyline { Layer = L(doc) };
		for (int i = 0; i < WidePolylinePoints; i++)
		{
			lw.Vertices.Add(new LwPolyline.Vertex(new XY(i * 0.5, (i % 7) * 0.25)));
		}

		doc.Entities.Add(lw);
		Write(doc, path);
	}

	public static void WriteLongText(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new MText
		{
			InsertPoint = new XYZ(0, 0, 0),
			Height = 1.0,
			Value = new string('V', LongTextBytes),
			Layer = L(doc),
		});
		Write(doc, path);
	}

	// 1x, 4x and 16x the same content, for the streaming measurement. The
	// shapes repeat on purpose: what changes between the three files is how
	// many records the decode produces and nothing else.
	public static void WriteScale(string path, int factor)
	{
		CadDocument doc = NewDoc();
		Layer layer = L(doc);
		int n = 64 * factor;
		for (int i = 0; i < n; i++)
		{
			double x = i * 0.5;
			doc.Entities.Add(new Line(new XYZ(x, 0, 0), new XYZ(x + 0.25, 1, 0)) { Layer = layer });
			doc.Entities.Add(new Circle { Center = new XYZ(x, 5, 0), Radius = 0.375, Layer = layer });
		}

		Write(doc, path);
	}

	public static void WriteManyInserts(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord block = new BlockRecord(BlockName);
		block.Entities.Add(new Line(new XYZ(0, 0, 0), new XYZ(1, 0, 0)));
		block.Entities.Add(new Circle { Center = new XYZ(0.5, 0.5, 0), Radius = 0.25 });
		block.Entities.Add(new Arc { Center = new XYZ(0, 1, 0), Radius = 0.5, StartAngle = 0, EndAngle = 1.5 });
		doc.BlockRecords.Add(block);

		Layer layer = L(doc);
		for (int i = 0; i < HostileInsertCount; i++)
		{
			doc.Entities.Add(new Insert(block)
			{
				InsertPoint = new XYZ((i % 100) * 2.0, (i / 100) * 2.0, 0),
				Layer = layer,
			});
		}

		Write(doc, path);
	}

	// The file the path-versus-memory measurement needs, and the one file in
	// the corpus that is generated at measurement time rather than committed.
	//
	// The claim is that open_path_utf8 does not duplicate the input to cross
	// FFI, and the size of that duplicate is the size of the file. A 180 KB
	// fixture cannot show it: the run-to-run spread of peak RSS on a managed
	// runtime is a couple of megabytes, so the signal has to be bigger than
	// that. Random coordinates under a fixed seed, because DWG compresses and
	// a file full of repeated numbers is not the size it looks.
	public static void WriteLarge(string path, int lines)
	{
		CadDocument doc = NewDoc();
		Layer layer = L(doc);
		Random rng = new Random(20260913);

		// Many small entities rather than a few enormous ones. ACadSharp's
		// writer costs far more per vertex inside one entity than it does per
		// entity, and a fixture that takes a quarter of an hour to write is a
		// fixture nobody regenerates. Random coordinates because DWG
		// compresses, and a file full of repeated numbers is not the size it
		// looks.
		for (int i = 0; i < lines; i++)
		{
			doc.Entities.Add(new Line(
				new XYZ(rng.NextDouble() * 1e6, rng.NextDouble() * 1e6, rng.NextDouble() * 1e3),
				new XYZ(rng.NextDouble() * 1e6, rng.NextDouble() * 1e6, rng.NextDouble() * 1e3))
			{
				Layer = layer,
			});
		}

		Write(doc, path);
	}

	// ------------------------------------------------- hostile but valid

	// The corpus needed a shape it did not have: files that open cleanly and
	// then make the walk do the damage.
	//
	// Every malformed derivative in this suite fails inside DwgReader at the
	// same line, so until these landed the flattener, the encoder and the
	// batch writer had never seen a byte of hostile input. Both Criticals the
	// review found live exactly there.

	// How deep the nested-dimension chain goes.
	//
	// The recursive walk this replaced died at roughly 2686 stack frames, and
	// it spent two or three of those per level, so it was gone somewhere
	// around a thousand. Three thousand is comfortably past that and still
	// writes a fixture of about a megabyte; every level is a generated
	// dimension block, so the file size is the depth.
	public const int DimensionChainDepth = 3000;

	// The shallow control: the same shape, inside every bound, decodes.
	public const int ShallowDimensionChainDepth = 4;

	// A chain of block records each holding this many insertions of the next.
	// Expansions are Fanout^Depth, and nothing in that expansion yields a
	// record, so the output-shaped bounds never see it.
	public const int FanoutWidth = 2;
	public const int FanoutDepth = 24;

	// The control: the same shape, small enough that the whole expansion fits
	// inside every default bound and decodes. Without it, "the fan-out is
	// refused" could be a decoder that refuses fan-outs.
	public const int ShallowFanoutDepth = 8;

	public const int WideSplineControlPoints = 20000;

	// There is no fixture past the 65536-byte default max_string_bytes, and
	// not for want of trying. ACadSharp's DWG round trip silently shortens a
	// long MText value: 8,192 characters come back whole, 40,000 come back as
	// 7,232, 70,000 as 4,464 and 200,000 as 3,392. So the writer cannot
	// produce a string that trips the default, and g13_long_text.dwg exercises
	// the bound with a limit the caller sets instead. A real drawing can still
	// carry one, and the bound is the same bound.

	private static DimensionLinear NewDim(double y, Layer layer)
	{
		return new DimensionLinear
		{
			FirstPoint = new XYZ(0, y, 0),
			SecondPoint = new XYZ(10, y, 0),
			DefinitionPoint = new XYZ(10, y, 0),
			TextMiddlePoint = new XYZ(5, y + 3, 0),
			Offset = 3,
			Layer = layer,
		};
	}

	// A chain of dimensions, each one living inside the previous one's block.
	//
	// Only the first is in model space, because ACadSharp gives every
	// CadObject exactly one owner and refuses a second.
	//
	// There is no cycle fixture beside this one, and not for want of trying.
	// The pinned writer cannot build a cycle of any shape: an entity cannot
	// have two owners, so two dimensions cannot hold each other, and
	// `new Insert(BlockRecord)` deep-clones the record it is handed, so two
	// block records cannot either (it recurses until the stack goes). A file
	// from another writer can carry one, and the walk refuses it exactly the
	// way it refuses this chain, by depth and by entity count, neither of
	// which cares whether the graph closes.
	private static void WriteDimensionChain(string path, int depth)
	{
		CadDocument doc = NewDoc();
		Layer layer = L(doc);

		DimensionLinear root = NewDim(0, layer);
		doc.Entities.Add(root);
		root.UpdateBlock();

		DimensionLinear previous = root;
		for (int i = 1; i < depth; i++)
		{
			DimensionLinear next = NewDim(i * 20.0, layer);
			previous.Block.Entities.Add(next);
			next.UpdateBlock();
			previous = next;
		}

		Write(doc, path);
	}

	public static void WriteDimensionDeep(string path)
	{
		WriteDimensionChain(path, DimensionChainDepth);
	}

	public static void WriteDimensionShallow(string path)
	{
		WriteDimensionChain(path, ShallowDimensionChainDepth);
	}

	// The expansion that emits nothing. Twenty-five block records and
	// forty-nine entities, and 2^24 expansions if nothing counts them.
	// The fan-out is built in memory and never written.
	//
	// ACadSharp's DwgWriter does not come back from it. `new Insert(record)`
	// clones the record when it belongs to a document, so a chain assembled
	// this way leaves the writer resolving references it cannot settle, and
	// the write never finishes. The reviewer who found this hole drove the
	// flattener over an in-memory document for the same reason.
	//
	// That costs nothing here: the hole was never about a file format. It is
	// about a walk that can do unbounded work while producing nothing, and the
	// document is what produces that, not the bytes it might have been stored
	// as.
	public static CadDocument BuildFanout(int depth, int width)
	{
		CadDocument doc = NewDoc();
		BlockRecord[] levels = new BlockRecord[depth + 1];
		for (int i = 0; i <= depth; i++)
		{
			levels[i] = new BlockRecord("VIPRS_G13_FANOUT_" + i);
			doc.BlockRecords.Add(levels[i]);
		}

		// The root insertion is made first, while the block it names is still
		// empty. `new Insert(record)` deep-clones a record that belongs to a
		// document, so making it last would clone the whole expansion it is
		// about to point at, which is the same exponential the fixture exists
		// to demonstrate and which never returns. Adding it to the document
		// resolves the clone back to the record by name.
		Insert root = new Insert(levels[0]) { InsertPoint = XYZ.Zero, Layer = L(doc) };
		doc.Entities.Add(root);

		// Each level is filled after the insertions naming it were made, for
		// the same reason and in the same order.
		for (int i = 0; i < depth; i++)
		{
			for (int k = 0; k < width; k++)
			{
				levels[i].Entities.Add(new Insert(levels[i + 1]) { InsertPoint = new XYZ(k, 0, 0) });
			}
		}

		// The deepest block is empty, so the whole expansion produces no
		// record at all: nothing to count against max_entities if the count is
		// of records, and no bytes to count against max_output_bytes.
		return doc;
	}

	// A spline wide enough that building its control points is a large
	// allocation, which is what max_polyline_points is there to refuse and
	// what the encoder's own guard never looked at.
	public static void WriteWideSpline(string path)
	{
		CadDocument doc = NewDoc();
		Spline s = new Spline { Degree = 3, Layer = L(doc) };
		for (int i = 0; i < WideSplineControlPoints; i++)
		{
			s.ControlPoints.Add(new XYZ(i * 0.25, (i % 11) * 0.5, 0));
		}

		for (int i = 0; i < WideSplineControlPoints + 4; i++)
		{
			s.Knots.Add(i * 0.001);
		}

		doc.Entities.Add(s);
		Write(doc, path);
	}

	// Not written by DwgWriter, because ACadSharp cannot produce an AC1009
	// file. Six bytes of signature and nothing else is all the version gate
	// looks at, which is the whole point: the refusal lands before anything
	// tries to parse the rest.
	public static void WriteAc1009(string path)
	{
		byte[] bytes = new byte[512];
		byte[] sig = System.Text.Encoding.ASCII.GetBytes("AC1009");
		Array.Copy(sig, bytes, sig.Length);
		File.WriteAllBytes(path, bytes);
	}
}
