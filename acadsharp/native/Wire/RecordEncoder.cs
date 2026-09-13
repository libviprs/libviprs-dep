using System;
using System.Text;
using Viprs.Abi;

// Turns one primitive into one record, exactly as docs/WIRE.md lays it out.
//
// Everything is written little-endian by hand rather than through BitConverter,
// so the bytes do not depend on the host the shim was built on. That is not
// theoretical: the shim ships for two architectures and the consumer may be on
// a third.
namespace Viprs.Wire
{
	internal sealed class RecordEncoder
	{
		private readonly ResolvedLimits _limits;
		private byte[] _buf = new byte[256];
		private int _pos;

		public RecordEncoder(ResolvedLimits limits)
		{
			_limits = limits ?? ResolvedLimits.Defaults;
		}

		// Encodes into an internal buffer and returns the record's length.
		// The bytes are valid until the next call, which is all the batch
		// writer needs and saves an allocation per record.
		public int Encode(Primitive p, out byte[] bytes)
		{
			_pos = 0;
			Reserve(WireFormat.RecordHeaderBytes);
			_pos = WireFormat.RecordHeaderBytes;

			switch (p.Type)
			{
				case WireFormat.TypeDocumentBegin:
					U32(p.Counts[0]);
					U32(p.Counts[1]);
					U64(0ul);
					break;

				case WireFormat.TypeViewBegin:
					U32(p.Counts[0]);
					U32(p.Counts[1]);
					Doubles(p.Values, 0, 4);
					U64(p.Count64);
					Utf8WithLength(p.Text);
					break;

				case WireFormat.TypeLine:
					Prologue(p);
					Doubles(p.Values, 0, 6);
					break;

				case WireFormat.TypePolyline:
					GuardPoints(p.Counts[0]);
					Prologue(p);
					U32(p.Counts[0]);
					U32(p.Counts[1]);
					Doubles(p.Values, 0, p.Values.Length);
					break;

				case WireFormat.TypeArc:
					Prologue(p);
					Doubles(p.Values, 0, 9);
					break;

				case WireFormat.TypeCircle:
					Prologue(p);
					Doubles(p.Values, 0, 7);
					break;

				case WireFormat.TypeEllipse:
					Prologue(p);
					Doubles(p.Values, 0, 12);
					break;

				case WireFormat.TypeSpline:
					Prologue(p);
					for (int i = 0; i < 6; i++)
					{
						U32(p.Counts[i]);
					}

					Doubles(p.Values, 0, p.Values.Length);
					break;

				case WireFormat.TypePolygon:
					GuardPoints(p.Counts[0]);
					Prologue(p);
					U32(p.Counts[0]);
					U32(p.Counts[1]);
					Doubles(p.Values, 0, p.Values.Length);
					break;

				case WireFormat.TypeText:
					Prologue(p);
					Doubles(p.Values, 0, 5);
					Utf8WithLength(p.Text);
					break;

				case WireFormat.TypeWarning:
					U32(p.Counts[0]);
					U32(0u);
					U64(p.ItemHandle);
					Utf8WithLength(p.Text);
					break;

				case WireFormat.TypeViewEnd:
					U32(p.Counts[0]);
					U32(0u);
					U64(p.Count64);
					break;

				case WireFormat.TypeDocumentEnd:
					U64(p.Count64);
					U64(p.ItemHandle);
					break;

				default:
					// The forward probe, and anything a later version adds.
					Raw(p.Raw ?? Array.Empty<byte>());
					break;
			}

			Pad();
			WriteHeader(p.Type, _pos);
			bytes = _buf;
			return _pos;
		}

		private void Prologue(Primitive p)
		{
			U64(p.ItemHandle);
			U32(p.Flags);
			U32(0u);
		}

		private void GuardPoints(uint count)
		{
			if ((ulong)count > _limits.MaxPolylinePoints)
			{
				throw new AbiException(
					Result.LimitExceeded,
					"a single record carries more vertices than max_polyline_points allows"
				);
			}
		}

		private void Utf8WithLength(string text)
		{
			byte[] utf8 = Encoding.UTF8.GetBytes(text ?? string.Empty);
			if ((ulong)utf8.Length > _limits.MaxStringBytes)
			{
				throw new AbiException(
					Result.LimitExceeded,
					"a string in this record is longer than max_string_bytes allows"
				);
			}

			U32((uint)utf8.Length);
			U32(0u);
			Raw(utf8);
		}

		private void WriteHeader(ushort type, int length)
		{
			_buf[0] = (byte)(type & 0xFF);
			_buf[1] = (byte)((type >> 8) & 0xFF);
			_buf[2] = 0;
			_buf[3] = 0;
			_buf[4] = (byte)(length & 0xFF);
			_buf[5] = (byte)((length >> 8) & 0xFF);
			_buf[6] = (byte)((length >> 16) & 0xFF);
			_buf[7] = (byte)((length >> 24) & 0xFF);
		}

		private void Pad()
		{
			int pad = WireFormat.PadTo4(_pos);
			Reserve(pad);
			for (int i = 0; i < pad; i++)
			{
				_buf[_pos + i] = 0;
			}

			_pos += pad;
		}

		private void Reserve(int extra)
		{
			int needed = _pos + extra;
			if (needed <= _buf.Length)
			{
				return;
			}

			int size = _buf.Length;
			while (size < needed)
			{
				size = size * 2;
			}

			byte[] grown = new byte[size];
			Buffer.BlockCopy(_buf, 0, grown, 0, _pos);
			_buf = grown;
		}

		private void U32(uint v)
		{
			Reserve(4);
			_buf[_pos] = (byte)(v & 0xFF);
			_buf[_pos + 1] = (byte)((v >> 8) & 0xFF);
			_buf[_pos + 2] = (byte)((v >> 16) & 0xFF);
			_buf[_pos + 3] = (byte)((v >> 24) & 0xFF);
			_pos += 4;
		}

		private void U64(ulong v)
		{
			Reserve(8);
			for (int i = 0; i < 8; i++)
			{
				_buf[_pos + i] = (byte)((v >> (8 * i)) & 0xFF);
			}

			_pos += 8;
		}

		private void Doubles(double[] values, int start, int count)
		{
			for (int i = 0; i < count; i++)
			{
				U64((ulong)BitConverter.DoubleToInt64Bits(values[start + i]));
			}
		}

		private void Raw(byte[] data)
		{
			Reserve(data.Length);
			Buffer.BlockCopy(data, 0, _buf, _pos, data.Length);
			_pos += data.Length;
		}
	}
}
