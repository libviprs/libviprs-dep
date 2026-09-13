using System;
using System.Collections.Generic;

namespace Viprs.Cad;

// One decode handle: viprs_acad_decode_begin, decode_next_batch and
// decode_close, with nothing in it that knows what a DWG is.
//
// The batch header below is the adapter's own framing and is deliberately
// minimal, because the batch protocol belongs to the wire layer. What this
// class fixes is the behaviour the header specifies and a consumer depends
// on: a batch never spans two calls, a buffer too small for the next batch is
// LIMIT_EXCEEDED with the needed size written back, the cancel flag is read
// between batches and nowhere else, and no record is ever produced twice.
public sealed class DecodeSession : IDisposable
{
	// wire_version, record_count, payload_bytes.
	public const int BatchHeaderBytes = 4 + 4 + 8;
	public const uint WireVersion = 1u;

	private readonly ICancelFlag _cancel;
	private readonly AdapterLimits _limits;
	private IEnumerator<Record> _records;
	private Record _held;
	private bool _exhausted;
	private bool _finished;
	private bool _closed;
	private bool _started;

	public ulong TotalBytes { get; private set; }

	public ulong RecordCount { get; private set; }

	public ulong BatchCount { get; private set; }

	// The bound that tripped, for a caller that wants to say which one to
	// raise rather than just that something was too big.
	public string FailedBound { get; private set; }

	public string FailureMessage { get; private set; }

	public DecodeSession(IEnumerable<Record> records, AdapterLimits limits, ICancelFlag cancel)
	{
		this._records = records.GetEnumerator();
		this._limits = limits ?? AdapterLimits.Defaults();
		this._cancel = cancel ?? NeverCanceled.Instance;
		HandleRegistry.DecodeOpened();
	}

	public uint NextBatch(byte[] buf, int offset, int cap, out int written, out bool done)
	{
		written = 0;
		done = this._finished;

		if (this._closed)
		{
			return AdapterResult.InvalidArgument;
		}

		if (this._finished)
		{
			return AdapterResult.Ok;
		}

		// Read between batches, never during one. The header says the caller
		// may flip it from another thread, so this is the only place that
		// looks and the answer holds for the whole batch.
		if (this._started && this._cancel.IsSet)
		{
			this._finished = true;
			this.FailedBound = "cancel_flag";
			this.FailureMessage = "the caller set cancel_flag between batches";
			return AdapterResult.Canceled;
		}

		this._started = true;

		if (buf is null || cap < BatchHeaderBytes)
		{
			written = BatchHeaderBytes;
			return AdapterResult.LimitExceeded;
		}

		int payload = 0;
		int count = 0;

		try
		{
			while (true)
			{
				if (this._held is null)
				{
					if (this._exhausted)
					{
						break;
					}

					if (!this._records.MoveNext())
					{
						this._exhausted = true;
						break;
					}

					this._held = this._records.Current;
				}

				int size = RecordEncoder.Size(this._held);
				if (BatchHeaderBytes + payload + size > cap)
				{
					if (count == 0)
					{
						// A batch never spans two calls, so a record that
						// cannot fit alone is a buffer the caller has to grow.
						written = BatchHeaderBytes + size;
						return AdapterResult.LimitExceeded;
					}

					break;
				}

				RecordEncoder.Write(this._held, buf, offset + BatchHeaderBytes + payload);
				payload += size;
				count++;
				this._held = null;
			}
		}
		catch (AdapterLimitException ex)
		{
			this._finished = true;
			this.FailedBound = ex.Bound;
			this.FailureMessage = ex.Message;
			return AdapterResult.LimitExceeded;
		}
		catch (AdapterCanceledException)
		{
			this._finished = true;
			this.FailedBound = "cancel_flag";
			this.FailureMessage = "the decode was canceled";
			return AdapterResult.Canceled;
		}
		catch (OutOfMemoryException)
		{
			this._finished = true;
			this.FailedBound = "memory";
			this.FailureMessage = "the runtime ran out of memory during the decode";
			return AdapterResult.OutOfMemory;
		}
		catch (Exception ex)
		{
			this._finished = true;
			this.FailedBound = "internal";
			this.FailureMessage = ex.GetType().FullName + ": " + ex.Message;
			return AdapterResult.CorruptInput;
		}

		WriteHeader(buf, offset, count, payload);
		written = BatchHeaderBytes + payload;
		this.TotalBytes += (ulong)written;
		this.RecordCount += (ulong)count;
		this.BatchCount++;

		if (this.TotalBytes > this._limits.MaxOutputBytes)
		{
			this._finished = true;
			this.FailedBound = "max_output_bytes";
			this.FailureMessage = "the decode has written " + this.TotalBytes
				+ " bytes and max_output_bytes is " + this._limits.MaxOutputBytes;
			written = 0;
			return AdapterResult.LimitExceeded;
		}

		if (this._exhausted && this._held is null)
		{
			this._finished = true;
			done = true;
		}

		return AdapterResult.Ok;
	}

	private static void WriteHeader(byte[] buf, int offset, int count, int payload)
	{
		WriteU32(buf, offset, WireVersion);
		WriteU32(buf, offset + 4, (uint)count);
		WriteU64(buf, offset + 8, (ulong)payload);
	}

	private static void WriteU32(byte[] buf, int at, uint v)
	{
		buf[at] = (byte)(v & 0xFF);
		buf[at + 1] = (byte)((v >> 8) & 0xFF);
		buf[at + 2] = (byte)((v >> 16) & 0xFF);
		buf[at + 3] = (byte)((v >> 24) & 0xFF);
	}

	private static void WriteU64(byte[] buf, int at, ulong v)
	{
		for (int i = 0; i < 8; i++)
		{
			buf[at + i] = (byte)((v >> (8 * i)) & 0xFF);
		}
	}

	public void Dispose()
	{
		if (this._closed)
		{
			return;
		}

		this._closed = true;
		if (this._records != null)
		{
			this._records.Dispose();
			this._records = null;
		}

		HandleRegistry.DecodeClosed();
	}
}
