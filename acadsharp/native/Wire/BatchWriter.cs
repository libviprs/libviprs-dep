using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using Viprs.Abi;

// Fills one caller-owned buffer with one batch, per docs/WIRE.md.
//
// The awkward part of a batch protocol is the record that does not fit, and
// the rule this implements is that a batch never splits one. So the writer
// looks at the next record before committing to it, and when the caller's
// buffer is too small for even a single record it says so with the size it
// needs rather than writing half of one.
//
// "Looks at" is the encoder's own overflow report rather than a trial encode
// into scratch memory: the record is encoded once, straight into the caller's
// buffer, and a record that does not fit costs a walk that writes nothing.
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

				// A record is written into the room the batch actually has,
				// never into the room the buffer has: the two differ once
				// MaxBatchBytes or the caller's own cap is the smaller, and
				// writing into the difference would scribble past what this
				// call reports as written.
				ulong committed = (ulong)(WireFormat.BatchHeaderBytes + payload);
				ulong limit = payload == 0 ? cap : ceiling;
				Span<byte> room = Room(buf, committed, limit);

				int length;
				if (!_encoder.TryEncode(next, room, out length))
				{
					if (payload == 0)
					{
						// A record larger than the batch ceiling becomes a
						// batch of its own, because the alternative is a
						// stream that cannot carry a drawing the limits allow.
						// Nothing was written, and the caller is told the size
						// to come back with.
						written = committed + (ulong)length;
						return Result.LimitExceeded;
					}

					// It does not fit beside what is already here. It stays
					// pending and starts the next batch, having cost a walk
					// rather than an encode.
					break;
				}

				// max_output_bytes, before the record is counted rather than
				// after the batch carrying it has been framed.
				//
				// Checking afterwards let the decode hand back one whole batch
				// past the ceiling, and the refusal arrived as a throw from a
				// call that had already filled the caller's buffer, so *written
				// came back 0 for bytes that were really there. Refusing here
				// means the bound is exact and the buffer holds only what the
				// call says it holds.
				ulong wouldEmit = _emitted + committed + (ulong)length;
				if (wouldEmit > _limits.MaxOutputBytes)
				{
					if (payload == 0)
					{
						written = 0ul;
						return Result.LimitExceeded;
					}

					break;
				}

				payload += length;
				Take();

				if (WireFormat.BatchHeaderBytes + payload >= WireFormat.TargetBatchBytes)
				{
					break;
				}
			}

			// The framing bytes count too. Every record above was checked
			// against the ceiling with this batch's header included, so this
			// can only fire on a batch that carries no record at all: the
			// empty last batch, or an empty document, whose twelve bytes would
			// otherwise be the one thing that crosses the bound.
			if (_emitted + (ulong)(WireFormat.BatchHeaderBytes + payload) > _limits.MaxOutputBytes)
			{
				written = 0ul;
				return Result.LimitExceeded;
			}

			ushort flags = Finished ? WireFormat.FlagLast : (ushort)0;
			WriteBatchHeader(buf, flags, (uint)payload);

			written = (ulong)(WireFormat.BatchHeaderBytes + payload);
			_emitted += written;
			done = Finished ? (byte)1 : (byte)0;
			_closed = Finished;
			return Result.Ok;
		}

		// The writable room at `at`, bounded by `limit` and by what a Span can
		// address. A caller may hand over more than int.MaxValue bytes and a
		// record is never that big, so clamping costs nothing and skipping it
		// would be an overflow.
		private static unsafe Span<byte> Room(byte* buf, ulong at, ulong limit)
		{
			if (limit <= at)
			{
				return default;
			}

			ulong room = limit - at;
			int length = room > (ulong)int.MaxValue ? int.MaxValue : (int)room;
			return new Span<byte>(buf + at, length);
		}

		private static unsafe void WriteBatchHeader(byte* buf, ushort flags, uint payloadLength)
		{
			Span<byte> header = new Span<byte>(buf, WireFormat.BatchHeaderBytes);
			WireFormat.Magic.CopyTo(header);
			BinaryPrimitives.WriteUInt16LittleEndian(header.Slice(4), WireFormat.Version);
			BinaryPrimitives.WriteUInt16LittleEndian(header.Slice(6), flags);
			BinaryPrimitives.WriteUInt32LittleEndian(header.Slice(8), payloadLength);
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
