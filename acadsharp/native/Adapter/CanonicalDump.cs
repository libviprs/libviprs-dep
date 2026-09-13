using System.Globalization;
using System.Text;

namespace Viprs.Cad;

// The canonical text form of one record, and the diff that names the first
// record two streams disagree about.
//
// Fixed precision on purpose. A round-trip through "R" or "0.############"
// makes the expectation a record of the last machine that wrote it, and the
// first cross-architecture rerun turns a committed file into a diff nobody
// can read. Six decimals is well inside what a DWG carries and well outside
// what the last bit of a double wobbles by.
public static class CanonicalDump
{
	private const string Fmt = "0.000000";

	public static string N(double d)
	{
		string s = d.ToString(Fmt, CultureInfo.InvariantCulture);
		// Negative zero prints as "-0.000000" and compares unequal to the
		// same number reached from the other side. It is the same point.
		if (s == "-0.000000")
		{
			s = "0.000000";
		}
		return s;
	}

	private static string V(Vec3 v)
	{
		return "(" + N(v.X) + "," + N(v.Y) + "," + N(v.Z) + ")";
	}

	private static string Q(string s)
	{
		if (s is null)
		{
			return "\"\"";
		}

		StringBuilder sb = new StringBuilder();
		sb.Append('"');
		foreach (char ch in s)
		{
			switch (ch)
			{
				case '"': sb.Append("\\\""); break;
				case '\\': sb.Append("\\\\"); break;
				case '\n': sb.Append("\\n"); break;
				case '\r': sb.Append("\\r"); break;
				case '\t': sb.Append("\\t"); break;
				default:
					if (ch < 0x20)
					{
						sb.Append("\\u").Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
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

	private static void Points(StringBuilder sb, Record r, bool bulges)
	{
		int n = r.Points is null ? 0 : r.Points.Length;
		sb.Append(" n=").Append(n.ToString(CultureInfo.InvariantCulture));
		sb.Append(" pts=[");
		for (int i = 0; i < n; i++)
		{
			if (i > 0)
			{
				sb.Append(';');
			}
			sb.Append(V(r.Points[i]));
			if (bulges)
			{
				sb.Append('@').Append(N(r.Bulges is null ? 0.0 : r.Bulges[i]));
			}
		}
		sb.Append(']');
	}

	private static void Doubles(StringBuilder sb, string name, double[] a)
	{
		int n = a is null ? 0 : a.Length;
		sb.Append(' ').Append(name).Append('=').Append(n.ToString(CultureInfo.InvariantCulture));
		sb.Append(" [");
		for (int i = 0; i < n; i++)
		{
			if (i > 0)
			{
				sb.Append(';');
			}
			sb.Append(N(a[i]));
		}
		sb.Append(']');
	}

	public static string Line(int index, Record r)
	{
		StringBuilder sb = new StringBuilder();
		sb.Append(index.ToString("00000", CultureInfo.InvariantCulture));
		sb.Append(' ').Append(r.Kind.ToString());
		sb.Append(" handle=").Append(r.Handle.ToString("X", CultureInfo.InvariantCulture));
		sb.Append(" layer=").Append(Q(r.Layer));

		switch (r.Kind)
		{
			case RecordKind.Line:
				sb.Append(" a=").Append(V(r.A)).Append(" b=").Append(V(r.B));
				break;
			case RecordKind.Circle:
				sb.Append(" c=").Append(V(r.A)).Append(" r=").Append(N(r.R))
					.Append(" normal=").Append(V(r.N));
				break;
			case RecordKind.Arc:
				sb.Append(" c=").Append(V(r.A)).Append(" r=").Append(N(r.R))
					.Append(" a0=").Append(N(r.A0)).Append(" a1=").Append(N(r.A1))
					.Append(" normal=").Append(V(r.N));
				break;
			case RecordKind.Ellipse:
				sb.Append(" c=").Append(V(r.A)).Append(" major=").Append(V(r.B))
					.Append(" ratio=").Append(N(r.R))
					.Append(" p0=").Append(N(r.A0)).Append(" p1=").Append(N(r.A1))
					.Append(" normal=").Append(V(r.N));
				break;
			case RecordKind.Polyline:
			case RecordKind.Polygon:
				sb.Append(" closed=").Append(r.Closed != 0 ? "1" : "0");
				Points(sb, r, true);
				break;
			case RecordKind.Spline:
				sb.Append(" degree=").Append(r.Degree.ToString(CultureInfo.InvariantCulture));
				sb.Append(" closed=").Append(r.Closed != 0 ? "1" : "0");
				Points(sb, r, false);
				Doubles(sb, "knots", r.Knots);
				Doubles(sb, "weights", r.Weights);
				break;
			case RecordKind.Text:
				sb.Append(" p=").Append(V(r.A)).Append(" h=").Append(N(r.R))
					.Append(" rot=").Append(N(r.A0))
					.Append(" value=").Append(Q(r.Text));
				break;
			case RecordKind.Warning:
				sb.Append(" code=").Append(r.Code)
					.Append(" message=").Append(Q(r.Text));
				break;
		}

		return sb.ToString();
	}

	// The first line the two streams disagree about, or null when they agree.
	// A count mismatch is reported at the first index one side does not have,
	// because "the file is shorter" is still a differing record and saying so
	// by index is what makes the failure readable.
	public static string FirstDifference(string[] expected, string[] actual)
	{
		int n = expected.Length < actual.Length ? expected.Length : actual.Length;
		for (int i = 0; i < n; i++)
		{
			if (expected[i] != actual[i])
			{
				return "record " + i.ToString(CultureInfo.InvariantCulture)
					+ " differs\n  expected: " + expected[i]
					+ "\n  actual:   " + actual[i];
			}
		}

		if (expected.Length != actual.Length)
		{
			int i = n;
			string missing = expected.Length > actual.Length
				? "expected: " + expected[i] + "\n  actual:   <end of stream>"
				: "expected: <end of stream>\n  actual:   " + actual[i];
			return "record " + i.ToString(CultureInfo.InvariantCulture)
				+ " differs, the streams are " + expected.Length + " and " + actual.Length
				+ " records long\n  " + missing;
		}

		return null;
	}
}
