using System.Collections.Generic;
using System.Globalization;
using System.Text;
using Viprs.Wire;

// The canonical text form of one primitive, and the diff that names the first
// record two streams disagree about.
//
// Fixed precision on purpose. A round trip through "R" or "0.############"
// makes the expectation a record of the last machine that wrote it, and the
// first cross-architecture rerun turns a committed file into a diff nobody can
// read. Six decimals is well inside what a DWG carries and well outside what
// the last bit of a double wobbles by.
namespace Viprs.Cad
{
	internal static class CanonicalDump
	{
		private const string Fmt = "0.000000";

		public static string N(double d)
		{
			string s = d.ToString(Fmt, CultureInfo.InvariantCulture);

			// Negative zero prints as "-0.000000" and compares unequal to the
			// same point reached from the other side. It is the same point.
			return s == "-0.000000" ? "0.000000" : s;
		}

		private static string Q(string s)
		{
			if (s == null)
			{
				return "\"\"";
			}

			StringBuilder sb = new StringBuilder();
			sb.Append('"');
			foreach (char ch in s)
			{
				switch (ch)
				{
					case '"':
						sb.Append("\\\"");
						break;
					case '\\':
						sb.Append("\\\\");
						break;
					case '\n':
						sb.Append("\\n");
						break;
					case '\r':
						sb.Append("\\r");
						break;
					case '\t':
						sb.Append("\\t");
						break;
					default:
						if (ch < 0x20)
						{
							sb.Append("\\u")
								.Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
						}
						else
						{
							sb.Append(ch);
						}

						break;
				}
			}

			sb.Append('"');
			return sb.ToString();
		}

		public static string TypeName(ushort type)
		{
			switch (type)
			{
				case WireFormat.TypeDocumentBegin: return "DocumentBegin";
				case WireFormat.TypeViewBegin: return "ViewBegin";
				case WireFormat.TypeLine: return "Line";
				case WireFormat.TypePolyline: return "Polyline";
				case WireFormat.TypeArc: return "Arc";
				case WireFormat.TypeCircle: return "Circle";
				case WireFormat.TypeEllipse: return "Ellipse";
				case WireFormat.TypeSpline: return "Spline";
				case WireFormat.TypePolygon: return "Polygon";
				case WireFormat.TypeText: return "Text";
				case WireFormat.TypeWarning: return "Warning";
				case WireFormat.TypeViewEnd: return "ViewEnd";
				case WireFormat.TypeDocumentEnd: return "DocumentEnd";
				default: return "Type" + type.ToString(CultureInfo.InvariantCulture);
			}
		}

		private static void Values(StringBuilder sb, string name, double[] v, int from, int count)
		{
			sb.Append(' ').Append(name).Append("=[");
			for (int i = 0; i < count; i++)
			{
				if (i > 0)
				{
					sb.Append(',');
				}

				sb.Append(N(v[from + i]));
			}

			sb.Append(']');
		}

		private static void Triples(StringBuilder sb, string name, double[] v, int from, int count)
		{
			sb.Append(' ').Append(name).Append("=[");
			for (int i = 0; i < count; i++)
			{
				if (i > 0)
				{
					sb.Append(';');
				}

				sb.Append('(')
					.Append(N(v[from + (i * 3)])).Append(',')
					.Append(N(v[from + (i * 3) + 1])).Append(',')
					.Append(N(v[from + (i * 3) + 2]))
					.Append(')');
			}

			sb.Append(']');
		}

		public static string Line(int index, Primitive p)
		{
			StringBuilder sb = new StringBuilder();
			sb.Append(index.ToString("00000", CultureInfo.InvariantCulture));
			sb.Append(' ').Append(TypeName(p.Type));
			sb.Append(" handle=").Append(p.ItemHandle.ToString("X", CultureInfo.InvariantCulture));
			sb.Append(" flags=").Append(p.Flags.ToString(CultureInfo.InvariantCulture));

			switch (p.Type)
			{
				case WireFormat.TypeLine:
					Triples(sb, "pts", p.Values, 0, 2);
					break;

				case WireFormat.TypeArc:
					Triples(sb, "c", p.Values, 0, 1);
					sb.Append(" r=").Append(N(p.Values[3]));
					sb.Append(" a0=").Append(N(p.Values[4]));
					sb.Append(" a1=").Append(N(p.Values[5]));
					Triples(sb, "normal", p.Values, 6, 1);
					break;

				case WireFormat.TypeCircle:
					Triples(sb, "c", p.Values, 0, 1);
					sb.Append(" r=").Append(N(p.Values[3]));
					Triples(sb, "normal", p.Values, 4, 1);
					break;

				case WireFormat.TypeEllipse:
					Triples(sb, "c", p.Values, 0, 1);
					Triples(sb, "major", p.Values, 3, 1);
					sb.Append(" ratio=").Append(N(p.Values[6]));
					sb.Append(" p0=").Append(N(p.Values[7]));
					sb.Append(" p1=").Append(N(p.Values[8]));
					Triples(sb, "normal", p.Values, 9, 1);
					break;

				case WireFormat.TypePolyline:
					sb.Append(" n=").Append(p.Counts[0].ToString(CultureInfo.InvariantCulture));
					sb.Append(" closed=").Append(p.Counts[1].ToString(CultureInfo.InvariantCulture));
					Triples(sb, "pts", p.Values, 0, (int)p.Counts[0]);
					break;

				case WireFormat.TypePolygon:
					sb.Append(" n=").Append(p.Counts[0].ToString(CultureInfo.InvariantCulture));
					Triples(sb, "pts", p.Values, 0, (int)p.Counts[0]);
					break;

				case WireFormat.TypeSpline:
				{
					sb.Append(" degree=").Append(p.Counts[0].ToString(CultureInfo.InvariantCulture));
					sb.Append(" splineflags=").Append(p.Counts[1].ToString(CultureInfo.InvariantCulture));
					int knots = (int)p.Counts[2];
					int control = (int)p.Counts[3];
					int weights = (int)p.Counts[4];
					Values(sb, "knots", p.Values, 0, knots);
					Triples(sb, "ctrl", p.Values, knots, control);
					Values(sb, "weights", p.Values, knots + (control * 3), weights);
					break;
				}

				case WireFormat.TypeText:
					Triples(sb, "p", p.Values, 0, 1);
					sb.Append(" h=").Append(N(p.Values[3]));
					sb.Append(" rot=").Append(N(p.Values[4]));
					sb.Append(" value=").Append(Q(p.Text));
					break;

				case WireFormat.TypeWarning:
					sb.Append(" code=").Append(WarningCodes.Name(p.Counts[0]));
					sb.Append(" message=").Append(Q(p.Text));
					break;
			}

			return sb.ToString();
		}

		// The first line the two streams disagree about, or null when they
		// agree. A count mismatch is reported at the first index one side
		// does not have, because "the file is shorter" is still a differing
		// record and saying so by index is what makes the failure readable.
		public static string FirstDifference(IList<string> expected, IList<string> actual)
		{
			int n = expected.Count < actual.Count ? expected.Count : actual.Count;
			for (int i = 0; i < n; i++)
			{
				if (expected[i] != actual[i])
				{
					return "record " + i.ToString(CultureInfo.InvariantCulture)
						+ " differs\n  expected: " + expected[i]
						+ "\n  actual:   " + actual[i];
				}
			}

			if (expected.Count != actual.Count)
			{
				int i = n;
				string missing = expected.Count > actual.Count
					? "expected: " + expected[i] + "\n  actual:   <end of stream>"
					: "expected: <end of stream>\n  actual:   " + actual[i];
				return "record " + i.ToString(CultureInfo.InvariantCulture)
					+ " differs, the streams are "
					+ expected.Count.ToString(CultureInfo.InvariantCulture) + " and "
					+ actual.Count.ToString(CultureInfo.InvariantCulture)
					+ " records long\n  " + missing;
			}

			return null;
		}
	}
}
