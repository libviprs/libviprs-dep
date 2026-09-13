using System;
using System.Text;

namespace Viprs.Cad;

// How many bytes a record costs, and the bytes themselves.
//
// This exists because the ABI makes the adapter enforce max_output_bytes:
// "total bytes the decode may emit across every batch". A bound on a number
// the flattener cannot compute is a bound nobody applies, so the flattener
// has to know the encoded size of every record it produces. Once it knows
// that, writing the bytes is the same walk, so it does both.
//
// The batch framing around these records belongs to the wire layer, which is
// not this file's to write. What is fixed here is the per-record shape, and
// the amplification numbers in tests/benchmarks are counted in these bytes.
public static class RecordEncoder
{
	// kind, flags, handle, payload length.
	public const int HeaderBytes = 4 + 4 + 8 + 4;

	private static int Utf8Bytes(string s)
	{
		return s is null ? 0 : Encoding.UTF8.GetByteCount(s);
	}

	private static int StringField(string s)
	{
		return 4 + Utf8Bytes(s);
	}

	public static int PayloadSize(Record r)
	{
		int n = StringField(r.Layer);
		switch (r.Kind)
		{
			case RecordKind.Line:
				return n + 8 * 6;
			case RecordKind.Circle:
				return n + 8 * 7;
			case RecordKind.Arc:
				return n + 8 * 9;
			case RecordKind.Ellipse:
				return n + 8 * 12;
			case RecordKind.Polyline:
			case RecordKind.Polygon:
				return n + 4 + (r.Points is null ? 0 : r.Points.Length * 32);
			case RecordKind.Spline:
				return n
					+ 4 + 4 + 4 + 4
					+ (r.Points is null ? 0 : r.Points.Length * 24)
					+ (r.Knots is null ? 0 : r.Knots.Length * 8)
					+ (r.Weights is null ? 0 : r.Weights.Length * 8);
			case RecordKind.Text:
				return n + 8 * 5 + StringField(r.Text);
			case RecordKind.Warning:
				return n + StringField(r.Code) + StringField(r.Text);
			default:
				return n;
		}
	}

	public static int Size(Record r)
	{
		return HeaderBytes + PayloadSize(r);
	}

	private sealed class Cursor
	{
		public byte[] Buffer;
		public int Offset;

		public void U32(uint v)
		{
			this.Buffer[this.Offset++] = (byte)(v & 0xFF);
			this.Buffer[this.Offset++] = (byte)((v >> 8) & 0xFF);
			this.Buffer[this.Offset++] = (byte)((v >> 16) & 0xFF);
			this.Buffer[this.Offset++] = (byte)((v >> 24) & 0xFF);
		}

		public void U64(ulong v)
		{
			for (int i = 0; i < 8; i++)
			{
				this.Buffer[this.Offset++] = (byte)((v >> (8 * i)) & 0xFF);
			}
		}

		public void F64(double v)
		{
			this.U64((ulong)BitConverter.DoubleToInt64Bits(v));
		}

		public void Vec(Vec3 v)
		{
			this.F64(v.X);
			this.F64(v.Y);
			this.F64(v.Z);
		}

		public void Str(string s)
		{
			byte[] b = s is null ? Array.Empty<byte>() : Encoding.UTF8.GetBytes(s);
			this.U32((uint)b.Length);
			System.Buffer.BlockCopy(b, 0, this.Buffer, this.Offset, b.Length);
			this.Offset += b.Length;
		}

		public void F64Array(double[] a)
		{
			int n = a is null ? 0 : a.Length;
			this.U32((uint)n);
			for (int i = 0; i < n; i++)
			{
				this.F64(a[i]);
			}
		}
	}

	// Writes the record at buf[offset..] and returns the byte count. The
	// caller has already checked there is room, because a batch never spans
	// two calls and deciding that is the session's job, not this one's.
	public static int Write(Record r, byte[] buf, int offset)
	{
		int payload = PayloadSize(r);
		Cursor c = new Cursor { Buffer = buf, Offset = offset };
		c.U32((uint)r.Kind);
		c.U32(r.Closed != 0 ? 1u : 0u);
		c.U64(r.Handle);
		c.U32((uint)payload);
		c.Str(r.Layer);

		switch (r.Kind)
		{
			case RecordKind.Line:
				c.Vec(r.A);
				c.Vec(r.B);
				break;
			case RecordKind.Circle:
				c.Vec(r.A);
				c.Vec(r.N);
				c.F64(r.R);
				break;
			case RecordKind.Arc:
				c.Vec(r.A);
				c.Vec(r.N);
				c.F64(r.R);
				c.F64(r.A0);
				c.F64(r.A1);
				break;
			case RecordKind.Ellipse:
				c.Vec(r.A);
				c.Vec(r.B);
				c.Vec(r.N);
				c.F64(r.R);
				c.F64(r.A0);
				c.F64(r.A1);
				break;
			case RecordKind.Polyline:
			case RecordKind.Polygon:
			{
				int n = r.Points is null ? 0 : r.Points.Length;
				c.U32((uint)n);
				for (int i = 0; i < n; i++)
				{
					c.Vec(r.Points[i]);
					c.F64(r.Bulges is null ? 0.0 : r.Bulges[i]);
				}
				break;
			}
			case RecordKind.Spline:
			{
				int n = r.Points is null ? 0 : r.Points.Length;
				c.U32((uint)r.Degree);
				c.U32((uint)n);
				for (int i = 0; i < n; i++)
				{
					c.Vec(r.Points[i]);
				}
				c.F64Array(r.Knots);
				c.F64Array(r.Weights);
				break;
			}
			case RecordKind.Text:
				c.Vec(r.A);
				c.F64(r.R);
				c.F64(r.A0);
				c.Str(r.Text);
				break;
			case RecordKind.Warning:
				c.Str(r.Code);
				c.Str(r.Text);
				break;
		}

		int written = c.Offset - offset;
		if (written != HeaderBytes + payload)
		{
			throw new InvalidOperationException(
				"record encoder wrote " + written + " bytes for a record it sized at "
				+ (HeaderBytes + payload) + ", so max_output_bytes counts something the "
				+ "stream does not contain");
		}

		return written;
	}
}
