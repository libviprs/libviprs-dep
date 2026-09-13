using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using ACadSharp;
using ACadSharp.Entities;
using ACadSharp.Header;
using ACadSharp.IO;
using ACadSharp.Objects;
using ACadSharp.Tables;
using CSMath;

namespace Viprs
{
	// One implementation, compiled into both the JIT console app and the
	// NativeAOT shared library, so a JIT/AOT difference can only come from the
	// runtime and the trimmer, never from two versions of the probe.
	public static class Probe
	{
		public const string FixtureText = "VIPRS-G11-TEXT";
		public const string FixtureBlockName = "VIPRS_G11_BLOCK";
		public const string FixtureLayerName = "VIPRS_G11_LAYER";
		public const string FixtureSavedBy = "VIPRS-G11";

		// Latin-1 that Windows-1252 can represent, then two characters it
		// cannot. Written into a pre-2007 DWG, where text is stored against a
		// code page rather than as unicode, this is what exercises
		// IO/CadReaderBase.getListedEncoding -> Encoding.GetEncoding(code).
		public const string FixtureCodePageText = "\u00C4\u00D6\u00DC-\u03A9-\u65E5\u672C";

		private static string N(double d) => d.ToString("0.############", CultureInfo.InvariantCulture);

		private static string S(string s) => s is null ? "null" : "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";

		private static string Xyz(XYZ p) => "[" + N(p.X) + "," + N(p.Y) + "," + N(p.Z) + "]";

		private static string Xy(XY p) => "[" + N(p.X) + "," + N(p.Y) + "]";

		// Non-default header values and layout extents, so a silently hollowed
		// system-variable map shows up as the ACadSharp default and not as a zero.
		public static CadDocument BuildFixture()
		{
			CadDocument doc = new CadDocument();

			doc.Header.LastSavedBy = FixtureSavedBy;
			doc.Header.TextHeightDefault = 7.25;
			doc.Header.Elevation = 3.5;
			doc.Header.AngularUnitPrecision = 6;
			doc.Header.DimensionScaleFactor = 12.5;

			Layout model = doc.Layouts[Layout.ModelLayoutName];
			model.MinExtents = new XYZ(-11.5, -22.25, -3.75);
			model.MaxExtents = new XYZ(101.5, 202.25, 33.75);
			model.MinLimits = new XY(-50.5, -60.25);
			model.MaxLimits = new XY(150.5, 260.25);

			Layer layer = new Layer(FixtureLayerName);
			doc.Layers.Add(layer);

			BlockRecord block = new BlockRecord(FixtureBlockName);
			block.Entities.Add(new Line(new XYZ(0, 0, 0), new XYZ(1, 1, 0)));
			doc.BlockRecords.Add(block);

			doc.Entities.Add(new Line(new XYZ(1.5, 2.5, 0), new XYZ(41.5, 62.5, 0)) { Layer = layer });
			doc.Entities.Add(new Circle { Center = new XYZ(10.25, 20.5, 0), Radius = 5.75 });
			doc.Entities.Add(new Arc { Center = new XYZ(30.25, 40.5, 0), Radius = 8.125, StartAngle = 0.25, EndAngle = 2.75 });
			doc.Entities.Add(new TextEntity { InsertPoint = new XYZ(2.5, 3.5, 0), Height = 4.5, Value = FixtureText });
			doc.Entities.Add(new Insert(block) { InsertPoint = new XYZ(60.5, 70.5, 0) });

			return doc;
		}

		public static void WriteFixture(string path)
		{
			DwgWriter.Write(path, BuildFixture());
		}

		// The same drawing written as AC1018 with text the code page cannot
		// fully represent. AC1032 stores text as unicode and never reaches the
		// encoding lookup, so a 2018 fixture would not test this at all.
		public static void WriteCodePageFixture(string path)
		{
			CadDocument doc = BuildFixture();
			doc.Header.Version = ACadVersion.AC1018;
			doc.Entities.Add(new TextEntity
			{
				InsertPoint = new XYZ(80.5, 90.5, 0),
				Height = 3.25,
				Value = FixtureCodePageText,
			});
			DwgWriter.Write(path, doc);
		}

		// The deliverable: a deterministic description of what the reader saw.
		// Any JIT/AOT divergence has to show up as a byte difference here.
		public static string Describe(string path)
		{
			CadDocument doc = DwgReader.Read(path);
			StringBuilder sb = new StringBuilder();

			sb.Append("{\n");
			sb.Append("  \"header_map_count\": ").Append(CadHeader.GetHeaderMap().Count).Append(",\n");
			sb.Append("  \"version\": ").Append(S(doc.Header.Version.ToString())).Append(",\n");
			sb.Append("  \"last_saved_by\": ").Append(S(doc.Header.LastSavedBy)).Append(",\n");
			sb.Append("  \"text_height_default\": ").Append(N(doc.Header.TextHeightDefault)).Append(",\n");
			sb.Append("  \"elevation\": ").Append(N(doc.Header.Elevation)).Append(",\n");
			sb.Append("  \"angular_unit_precision\": ").Append(doc.Header.AngularUnitPrecision.ToString(CultureInfo.InvariantCulture)).Append(",\n");
			sb.Append("  \"dimension_scale_factor\": ").Append(N(doc.Header.DimensionScaleFactor)).Append(",\n");

			List<string> layerNames = new List<string>();
			foreach (Layer l in doc.Layers)
			{
				layerNames.Add(l.Name);
			}
			layerNames.Sort(StringComparer.Ordinal);
			sb.Append("  \"layer_count\": ").Append(doc.Layers.Count).Append(",\n");
			sb.Append("  \"layers\": ").Append(Join(layerNames)).Append(",\n");

			List<string> blockNames = new List<string>();
			foreach (BlockRecord b in doc.BlockRecords)
			{
				blockNames.Add(b.Name);
			}
			blockNames.Sort(StringComparer.Ordinal);
			sb.Append("  \"block_record_count\": ").Append(doc.BlockRecords.Count).Append(",\n");
			sb.Append("  \"block_records\": ").Append(Join(blockNames)).Append(",\n");
			sb.Append("  \"text_style_count\": ").Append(doc.TextStyles.Count).Append(",\n");
			sb.Append("  \"line_type_count\": ").Append(doc.LineTypes.Count).Append(",\n");

			List<string> layoutNames = new List<string>();
			foreach (Layout lay in doc.Layouts)
			{
				layoutNames.Add(lay.Name);
			}
			layoutNames.Sort(StringComparer.Ordinal);
			sb.Append("  \"layout_count\": ").Append(layoutNames.Count).Append(",\n");
			sb.Append("  \"layouts\": ").Append(Join(layoutNames)).Append(",\n");

			Layout modelLayout = null;
			foreach (Layout lay in doc.Layouts)
			{
				if (string.Equals(lay.Name, Layout.ModelLayoutName, StringComparison.Ordinal))
				{
					modelLayout = lay;
				}
			}
			if (modelLayout is null)
			{
				sb.Append("  \"model_layout\": null,\n");
			}
			else
			{
				sb.Append("  \"model_layout\": {\"min_extents\": ").Append(Xyz(modelLayout.MinExtents))
					.Append(", \"max_extents\": ").Append(Xyz(modelLayout.MaxExtents))
					.Append(", \"min_limits\": ").Append(Xy(modelLayout.MinLimits))
					.Append(", \"max_limits\": ").Append(Xy(modelLayout.MaxLimits))
					.Append("},\n");
			}

			SortedDictionary<string, int> byType = new SortedDictionary<string, int>(StringComparer.Ordinal);
			List<string> details = new List<string>();
			foreach (Entity e in doc.Entities)
			{
				string t = e.GetType().Name;
				byType.TryGetValue(t, out int c);
				byType[t] = c + 1;
				details.Add(DescribeEntity(e));
			}
			details.Sort(StringComparer.Ordinal);

			sb.Append("  \"entity_count\": ").Append(doc.Entities.Count).Append(",\n");
			sb.Append("  \"entities_by_type\": {");
			bool first = true;
			foreach (KeyValuePair<string, int> kv in byType)
			{
				if (!first)
				{
					sb.Append(", ");
				}
				first = false;
				sb.Append(S(kv.Key)).Append(": ").Append(kv.Value);
			}
			sb.Append("},\n");

			sb.Append("  \"entity_details\": [\n");
			for (int i = 0; i < details.Count; i++)
			{
				sb.Append("    ").Append(S(details[i]));
				if (i != details.Count - 1)
				{
					sb.Append(',');
				}
				sb.Append('\n');
			}
			sb.Append("  ]\n}");
			return sb.ToString();
		}

		private static string Join(List<string> names)
		{
			StringBuilder sb = new StringBuilder("[");
			for (int i = 0; i < names.Count; i++)
			{
				if (i != 0)
				{
					sb.Append(", ");
				}
				sb.Append(S(names[i]));
			}
			sb.Append(']');
			return sb.ToString();
		}

		private static string DescribeEntity(Entity e)
		{
			string layer = e.Layer is null ? "?" : e.Layer.Name;
			switch (e)
			{
				case Line l:
					return "Line layer=" + layer + " start=" + Xyz(l.StartPoint) + " end=" + Xyz(l.EndPoint);
				case Arc a:
					return "Arc layer=" + layer + " center=" + Xyz(a.Center) + " r=" + N(a.Radius)
						+ " start=" + N(a.StartAngle) + " end=" + N(a.EndAngle);
				case Circle c:
					return "Circle layer=" + layer + " center=" + Xyz(c.Center) + " r=" + N(c.Radius);
				case TextEntity t:
					return "TextEntity layer=" + layer + " at=" + Xyz(t.InsertPoint) + " h=" + N(t.Height) + " value=" + t.Value;
				case Insert ins:
					return "Insert layer=" + layer + " at=" + Xyz(ins.InsertPoint) + " block=" + (ins.Block is null ? "?" : ins.Block.Name);
				default:
					return e.GetType().Name + " layer=" + layer;
			}
		}
	}
}
