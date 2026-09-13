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

	public static IEnumerable<string> WriteAll(string dir)
	{
		Directory.CreateDirectory(dir);
		List<string> written = new List<string>();

		foreach (KeyValuePair<string, Action<string>> kv in Writers())
		{
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
		yield return Pair("g13_xref.dwg", WriteXref);
		yield return Pair("g13_nonuniform.dwg", WriteNonUniform);
		yield return Pair("g13_two_entities.dwg", WriteTwoEntities);
		yield return Pair("g13_deep_blocks.dwg", WriteDeepBlocks);
		yield return Pair("g13_wide_polyline.dwg", WriteWidePolyline);
		yield return Pair("g13_long_text.dwg", WriteLongText);
		yield return Pair("g13_scale_1x.dwg", p => WriteScale(p, 1));
		yield return Pair("g13_scale_4x.dwg", p => WriteScale(p, 4));
		yield return Pair("g13_scale_16x.dwg", p => WriteScale(p, 16));
		yield return Pair("g13_many_inserts.dwg", WriteManyInserts);
		yield return Pair("g13_ac1009.dwg", WriteAc1009);
		yield return Pair("g13_dimension_cycle.dwg", WriteDimensionCycle);
		yield return Pair("g13_dimension_deep.dwg", WriteDimensionDeep);
		yield return Pair("g13_dimension_shallow.dwg", WriteDimensionShallow);
		yield return Pair("g13_fanout.dwg", WriteFanout);
		yield return Pair("g13_wide_spline.dwg", WriteWideSpline);
		yield return Pair("g13_huge_text.dwg", WriteHugeText);
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
		// one is still one polygon and no curve is approximated.
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

	public static void WriteXref(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord xref = new BlockRecord(XrefBlockName, XrefPath);
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

	// How deep the nested-dimension chain goes. Well past the roughly 2686
	// stack frames the recursive walk died at, so a build that still recurses
	// aborts rather than passing because the fixture was too small.
	public const int DimensionChainDepth = 6000;

	// The shallow control: the same shape, inside every bound, decodes.
	public const int ShallowDimensionChainDepth = 4;

	// A chain of block records each holding this many insertions of the next.
	// Expansions are Fanout^Depth, and nothing in that expansion yields a
	// record, so the output-shaped bounds never see it.
	public const int FanoutWidth = 2;
	public const int FanoutDepth = 24;

	public const int WideSplineControlPoints = 20000;

	// Past the 65536-byte default max_string_bytes, so this one refuses at the
	// defaults rather than needing a limit set for it.
	public const int HugeTextBytes = 200000;

	private static DimensionLinear Dim(CadDocument doc, double y)
	{
		DimensionLinear dim = new DimensionLinear
		{
			FirstPoint = new XYZ(0, y, 0),
			SecondPoint = new XYZ(10, y, 0),
			DefinitionPoint = new XYZ(10, y, 0),
			TextMiddlePoint = new XYZ(5, y + 3, 0),
			Offset = 3,
			Layer = L(doc),
		};
		doc.Entities.Add(dim);
		dim.UpdateBlock();
		return dim;
	}

	// Two dimensions whose blocks hold each other. Walking one walks the
	// other, forever, and depth is not the thing that stops it: the cycle is
	// two levels wide.
	public static void WriteDimensionCycle(string path)
	{
		CadDocument doc = NewDoc();
		DimensionLinear a = Dim(doc, 0);
		DimensionLinear b = Dim(doc, 20);
		a.Block.Entities.Add(b);
		b.Block.Entities.Add(a);
		Write(doc, path);
	}

	private static void WriteDimensionChain(string path, int depth)
	{
		CadDocument doc = NewDoc();
		DimensionLinear[] chain = new DimensionLinear[depth];
		for (int i = 0; i < depth; i++)
		{
			chain[i] = Dim(doc, i * 20.0);
		}

		// Each dimension's picture holds the next one, so a walk that follows
		// a dimension block without bounding its depth follows all of them.
		for (int i = 0; i < depth - 1; i++)
		{
			chain[i].Block.Entities.Add(chain[i + 1]);
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
	public static void WriteFanout(string path)
	{
		CadDocument doc = NewDoc();
		BlockRecord[] levels = new BlockRecord[FanoutDepth + 1];
		for (int i = 0; i <= FanoutDepth; i++)
		{
			levels[i] = new BlockRecord("VIPRS_G13_FANOUT_" + i);
			doc.BlockRecords.Add(levels[i]);
		}

		for (int i = 0; i < FanoutDepth; i++)
		{
			for (int k = 0; k < FanoutWidth; k++)
			{
				levels[i].Entities.Add(new Insert(levels[i + 1]) { InsertPoint = new XYZ(k, 0, 0) });
			}
		}

		// The deepest block is empty, so the whole expansion produces no
		// record at all.
		doc.Entities.Add(new Insert(levels[0]) { InsertPoint = XYZ.Zero, Layer = L(doc) });
		Write(doc, path);
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

	// A text past the default max_string_bytes, so the refusal happens with
	// no limit set by the caller at all.
	public static void WriteHugeText(string path)
	{
		CadDocument doc = NewDoc();
		doc.Entities.Add(new MText
		{
			InsertPoint = new XYZ(0, 0, 0),
			Height = 1.0,
			Value = new string('W', HugeTextBytes),
			Layer = L(doc),
		});
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
