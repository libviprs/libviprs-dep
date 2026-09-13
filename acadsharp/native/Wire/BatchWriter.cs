using System.Collections.Generic;
using Viprs.Abi;

// Fills one caller-owned buffer with one batch, per docs/WIRE.md.
//
// The awkward part of a batch protocol is the record that does not fit, and
// the rule this implements is that a batch never splits one. So the writer
// looks at the next record before committing to it, and when the caller's
// buffer is too small for even a single record it says so with the size it
// needs rather than writing half of one.
namespace Viprs.Wire
{
	internal sealed class BatchWriter
	{
		private readonly IEnumerator<Primitive> _stream;
		private readonly RecordEncoder _encoder;
		private readonly ResolvedLimits _limits;

		private Primitive _pending;
		private bool _exhausted;
		private bool _closed;
		private ulong _emitted;

		public BatchWriter(IEnumerator<Primitive> stream, ResolvedLimits limits)
		{
			_stream = stream;
			_limits = limits ?? ResolvedLimits.Defaults;
			_encoder = new RecordEncoder(_limits);
		}

		public bool Finished
		{
			get { return _exhausted && _pending == null; }
		}

		public unsafe uint NextBatch(byte* buf, ulong cap, out ulong written, out byte done)
		{
			written = 0ul;
			done = 0;

			// The batch header goes out whatever else happens, so the buffer
			// has to hold it before anything is written. Without this check
			// those twelve bytes land past the end of a shorter buffer and
			// the call still reports OK with *written 12, which is the one
			// shape a caller cannot defend against: it has been told the call
			// succeeded and that twelve bytes are there to read.
			if (cap < (ulong)WireFormat.BatchHeaderBytes)
			{
				written = (ulong)WireFormat.BatchHeaderBytes;
				return Result.LimitExceeded;
			}

			// Everything was said on an earlier call. Another call is legal
			// and gets an empty final batch, so a loop that keeps asking
			// terminates instead of failing, and it is not counted against
			// max_output_bytes because the decode is not producing anything.
			if (_closed)
			{
				WriteBatchHeader(buf, WireFormat.FlagLast, 0u);
				written = (ulong)WireFormat.BatchHeaderBytes;
				done = 1;
				return Result.Ok;
			}

			int payload = 0;
			ulong ceiling = cap < (ulong)WireFormat.MaxBatchBytes
				? cap
				: (ulong)WireFormat.MaxBatchBytes;

			while (true)
			{
				Primitive next = Peek();
				if (next == null)
				{
					break;
				}

				byte[] bytes;
				int length = _encoder.Encode(next, out bytes);
				ulong total = (ulong)(WireFormat.BatchHeaderBytes + payload + length);

				if (payload == 0)
				{
					// A record larger than the batch ceiling becomes a batch
					// of its own, because the alternative is a stream that
					// cannot carry a drawing the limits allow.
					if (total > cap)
					{
						written = total;
						return Result.LimitExceeded;
					}
				}
				else if (total > ceiling)
				{
					break;
				}

				for (int i = 0; i < length; i++)
				{
					buf[WireFormat.BatchHeaderBytes + payload + i] = bytes[i];
				}

				payload += length;
				Take();

				if (WireFormat.BatchHeaderBytes + payload >= WireFormat.TargetBatchBytes)
				{
					break;
				}
			}

			ushort flags = Finished ? WireFormat.FlagLast : (ushort)0;
			WriteBatchHeader(buf, flags, (uint)payload);

			written = (ulong)(WireFormat.BatchHeaderBytes + payload);
			_emitted += written;
			if (_emitted > _limits.MaxOutputBytes)
			{
				throw new AbiException(
					Result.LimitExceeded,
					"the decode has produced more bytes than max_output_bytes allows"
				);
			}

			done = Finished ? (byte)1 : (byte)0;
			_closed = Finished;
			return Result.Ok;
		}

		private static unsafe void WriteBatchHeader(byte* buf, ushort flags, uint payloadLength)
		{
			for (int i = 0; i < WireFormat.Magic.Length; i++)
			{
				buf[i] = WireFormat.Magic[i];
			}

			buf[4] = (byte)(WireFormat.Version & 0xFF);
			buf[5] = (byte)((WireFormat.Version >> 8) & 0xFF);
			buf[6] = (byte)(flags & 0xFF);
			buf[7] = (byte)((flags >> 8) & 0xFF);
			buf[8] = (byte)(payloadLength & 0xFF);
			buf[9] = (byte)((payloadLength >> 8) & 0xFF);
			buf[10] = (byte)((payloadLength >> 16) & 0xFF);
			buf[11] = (byte)((payloadLength >> 24) & 0xFF);
		}

		private Primitive Peek()
		{
			if (_pending != null || _exhausted)
			{
				return _pending;
			}

			if (_stream.MoveNext())
			{
				_pending = _stream.Current;
			}
			else
			{
				_exhausted = true;
			}

			return _pending;
		}

		private void Take()
		{
			_pending = null;
		}
	}
}
