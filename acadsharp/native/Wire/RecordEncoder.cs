using System;
using System.Buffers.Binary;
using System.Text;
using Viprs.Abi;

// Turns one primitive into one record, exactly as docs/WIRE.md lays it out.
//
// The bytes are written little-endian whatever the host is, because the shim
// ships for two architectures and the consumer may be on a third. That used to
// be a hand-rolled shift-and-store loop per scalar; it is
// BinaryPrimitives.Write*LittleEndian now, which is explicitly little-endian on
// every host and which RyuJIT turns into a store rather than eight.
//
// There is no intermediate buffer any more either. A record is written straight
// into the caller's batch, so a coordinate's bytes are touched once instead of
// twice, and the record that does not fit in the batch under construction
// reports the size it needs without a single byte being written anywhere.
namespace Viprs.Wire
{
	internal sealed class RecordEncoder
	{
		private readonly ResolvedLimits _limits;

		public RecordEncoder(ResolvedLimits limits)
		{
			_limits = limits ?? ResolvedLimits.Defaults;
		}

		// Writes one record into `dest` and reports its length.
		//
		// False means `dest` was too short, nothing was written, and `written`
		// is the length the record needs: that is the size-it-needs path, and
		// it costs a walk of the record rather than an encode of it.
		//
		// A bound the record breaches, and the finiteness guarantee, are a
		// throw either way and are checked whether or not the bytes land, so
		// a caller sizing a buffer for a record max_string_bytes forbids is
		// refused rather than told a size.
		public bool TryEncode(Primitive p, Span<byte> dest, out int written)
		{
			Cursor c = new Cursor(dest, false);
			Write(p, ref c);
			written = c.Length;

			if (c.Overflowed)
			{
				Agree(p, written);
				return false;
			}

			WriteHeader(dest, p.Type, written);
			Agree(p, written);
			return true;
		}

		// The length TryEncode would report, without a buffer.
		//
		// It is the same walk with the writes turned off rather than a second
		// piece of size arithmetic, because two copies of the size of a record
		// is exactly the kind of duplication that disagrees quietly. The test
		// build asserts the two agree on every record anyway.
		public int Measure(Primitive p)
		{
			Cursor c = new Cursor(default, true);
			Write(p, ref c);
			return c.Length;
		}

		[System.Diagnostics.Conditional("DEBUG")]
		[System.Diagnostics.Conditional("VIPRS_ACAD_TEST_EXPORTS")]
		private void Agree(Primitive p, int written)
		{
			int measured = Measure(p);
			if (measured != written)
			{
				throw new AbiException(
					Result.InternalError,
					"the encoder measured a record at "
						+ measured
						+ " bytes and wrote "
						+ written
				);
			}
		}

		// One walk of one record. Every scalar goes through the cursor, so the
		// counting pass and the writing pass cannot drift apart.
		private void Write(Primitive p, ref Cursor c)
		{
			c.Skip(WireFormat.RecordHeaderBytes);

			switch (p.Type)
			{
				case WireFormat.TypeDocumentBegin:
					c.U32(p.Counts[0]);
					c.U32(p.Counts[1]);
					c.U64(0ul);
					break;

				case WireFormat.TypeViewBegin:
					c.U32(p.Counts[0]);
					c.U32(p.Counts[1]);
					c.Doubles(p.Values, 0, 4);
					c.U64(p.Count64);
					Utf8WithLength(p.Text, ref c);
					break;

				case WireFormat.TypeLine:
					Prologue(p, ref c);
					c.GeometryDoubles(p.Values, 0, 6);
					break;

				case WireFormat.TypePolyline:
					GuardPoints(p.Counts[0]);
					Prologue(p, ref c);
					c.U32(p.Counts[0]);
					c.U32(p.Counts[1]);
					c.U32(p.Counts[2]);
					c.U32(p.Counts[3]);
					c.GeometryDoubles(p.Values, 0, p.Values.Length);
					break;

				case WireFormat.TypeArc:
					Prologue(p, ref c);
					c.GeometryDoubles(p.Values, 0, 9);
					break;

				case WireFormat.TypeCircle:
					Prologue(p, ref c);
					c.GeometryDoubles(p.Values, 0, 7);
					break;

				case WireFormat.TypeEllipse:
					Prologue(p, ref c);
					c.GeometryDoubles(p.Values, 0, 12);
					break;

				case WireFormat.TypeSpline:
					Prologue(p, ref c);
					for (int i = 0; i < 6; i++)
					{
						c.U32(p.Counts[i]);
					}

					c.GeometryDoubles(p.Values, 0, p.Values.Length);
					break;

				case WireFormat.TypePolygon:
					GuardPoints(p.Counts[0]);
					Prologue(p, ref c);
					c.U32(p.Counts[0]);
					c.U32(p.Counts[1]);
					c.U32(p.Counts[2]);
					c.U32(p.Counts[3]);
					c.GeometryDoubles(p.Values, 0, p.Values.Length);
					break;

				case WireFormat.TypeText:
					Prologue(p, ref c);
					c.GeometryDoubles(p.Values, 0, 5);
					Utf8WithLength(p.Text, ref c);
					break;

				case WireFormat.TypeWarning:
					c.U32(p.Counts[0]);
					c.U32(0u);
					c.U64(p.ItemHandle);
					Utf8WithLength(p.Text, ref c);
					break;

				case WireFormat.TypeViewEnd:
					c.U32(p.Counts[0]);
					c.U32(0u);
					c.U64(p.Count64);
					break;

				case WireFormat.TypeDocumentEnd:
					c.U64(p.Count64);
					c.U64(p.ItemHandle);
					break;

				default:
					// The forward probe, and anything a later version adds.
					c.Raw(p.Raw ?? Array.Empty<byte>());
					break;
			}

			c.Zero(WireFormat.PadTo4(c.Length));
		}

		private static void Prologue(Primitive p, ref Cursor c)
		{
			c.U64(p.ItemHandle);
			c.U32(p.Flags);
			c.U32(0u);
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

		// The length is counted before the bytes exist, and the bound is
		// checked against that count.
		//
		// It used to encode the string into a fresh byte[] and then compare the
		// array's length to max_string_bytes, so the one allocation the bound is
		// there to prevent happened first every time. GetByteCount answers the
		// same question without asking the allocator for anything.
		private void Utf8WithLength(string text, ref Cursor c)
		{
			string value = text ?? string.Empty;
			int bytes = Encoding.UTF8.GetByteCount(value);
			if ((ulong)bytes > _limits.MaxStringBytes)
			{
				throw new AbiException(
					Result.LimitExceeded,
					"a string in this record is longer than max_string_bytes allows"
				);
			}

			c.U32((uint)bytes);
			c.U32(0u);
			c.Utf8(value, bytes);
		}

		private static void WriteHeader(Span<byte> dest, ushort type, int length)
		{
			BinaryPrimitives.WriteUInt16LittleEndian(dest, type);
			BinaryPrimitives.WriteUInt16LittleEndian(dest.Slice(2), 0);
			BinaryPrimitives.WriteInt32LittleEndian(dest.Slice(4), length);
		}

		// A position in the destination that keeps counting after it runs out
		// of room.
		//
		// That is the whole trick: a cursor that stops writing but does not stop
		// measuring turns "this does not fit" and "this is how big it is" into
		// one pass over the record instead of two.
		private ref struct Cursor
		{
			private readonly Span<byte> _dest;
			private readonly bool _measureOnly;
			private int _pos;
			private bool _overflow;

			public Cursor(Span<byte> dest, bool measureOnly)
			{
				_dest = dest;
				_measureOnly = measureOnly;
				_pos = 0;
				_overflow = false;
			}

			public int Length
			{
				get { return _pos; }
			}

			public bool Overflowed
			{
				get { return _overflow; }
			}

			// The span to write `n` bytes into, or an empty one when this pass
			// is only counting or has already run past the end.
			private Span<byte> Take(int n)
			{
				int at = _pos;
				_pos = _pos + n;
				if (_measureOnly || _overflow || n == 0)
				{
					return default;
				}

				if (at + n > _dest.Length)
				{
					_overflow = true;
					return default;
				}

				return _dest.Slice(at, n);
			}

			public void Skip(int n)
			{
				Span<byte> at = Take(n);
				if (!at.IsEmpty)
				{
					at.Clear();
				}
			}

			public void Zero(int n)
			{
				Skip(n);
			}

			public void U32(uint v)
			{
				Span<byte> at = Take(4);
				if (!at.IsEmpty)
				{
					BinaryPrimitives.WriteUInt32LittleEndian(at, v);
				}
			}

			public void U64(ulong v)
			{
				Span<byte> at = Take(8);
				if (!at.IsEmpty)
				{
					BinaryPrimitives.WriteUInt64LittleEndian(at, v);
				}
			}

			public void Doubles(double[] values, int start, int count)
			{
				Span<byte> at = Take(count * 8);
				if (at.IsEmpty)
				{
					return;
				}

				for (int i = 0; i < count; i++)
				{
					BinaryPrimitives.WriteDoubleLittleEndian(
						at.Slice(i * 8, 8),
						values[start + i]
					);
				}
			}

			// The same, for a geometry record, plus docs/WIRE.md's producer
			// guarantee: no record of type 3 to 10 carries an f64 that is NaN
			// or infinite.
			//
			// A backstop, not the check. The walk replaces a primitive carrying
			// one with a NON_FINITE_GEOMETRY warning long before the encoder
			// sees it, and that is where the failure belongs: there the handle
			// is known, the record is nameable, and the decode carries on and
			// produces the rest of the drawing. Reaching here means that guard
			// did not run, which is a bug in this library rather than a fact
			// about the drawing, and INTERNAL_ERROR is exactly what docs/ABI.md
			// says that is.
			//
			// Checked on the counting pass as well as the writing one. The
			// guarantee is about the record, not about the buffer it lands in,
			// and a record that only ever overflowed a caller's batch would
			// otherwise cross it unchecked.
			//
			// ViewBegin goes through Doubles instead. Its extents are a
			// bounding box the source reports rather than a shape anybody
			// draws, and a view holding nothing has no finite one.
			public void GeometryDoubles(double[] values, int start, int count)
			{
				Span<byte> at = Take(count * 8);
				if (at.IsEmpty)
				{
					for (int i = 0; i < count; i++)
					{
						RequireFinite(values[start + i]);
					}

					return;
				}

				for (int i = 0; i < count; i++)
				{
					double v = values[start + i];
					RequireFinite(v);
					BinaryPrimitives.WriteDoubleLittleEndian(at.Slice(i * 8, 8), v);
				}
			}

			private static void RequireFinite(double v)
			{
				if (double.IsFinite(v))
				{
					return;
				}

				throw new AbiException(
					Result.InternalError,
					"a geometry record reached the encoder carrying a value that is "
						+ "not finite, which docs/WIRE.md promises never crosses. The "
						+ "walk's own guard should have replaced it with a warning."
				);
			}

			public void Utf8(string value, int bytes)
			{
				Span<byte> at = Take(bytes);
				if (!at.IsEmpty)
				{
					Encoding.UTF8.GetBytes(value, at);
				}
			}

			public void Raw(byte[] data)
			{
				Span<byte> at = Take(data.Length);
				if (!at.IsEmpty)
				{
					data.CopyTo(at);
				}
			}
		}
	}
}
