using System;
using System.Collections.Generic;
using System.Threading;
using Viprs.Abi;
using Viprs.Cad;
using Viprs.Sources;

// What an open document and an open decode actually are behind the two opaque
// handle types, and the composition of the record stream those handles serve.
namespace Viprs.Wire
{
	internal sealed class DocumentHandle : IDisposable
	{
		public readonly IDocumentSource Source;
		public readonly ResolvedLimits Limits;

		private readonly List<IntPtr> _decodes = new List<IntPtr>();
		private bool _closed;

		public DocumentHandle(IDocumentSource source, ResolvedLimits limits)
		{
			Source = source;
			Limits = limits;
		}

		public bool Closed
		{
			get { return _closed; }
		}

		public void Track(IntPtr decode)
		{
			lock (_decodes)
			{
				_decodes.Add(decode);
			}
		}

		// The other half of Track, and it was missing.
		//
		// decode_close took the handle out of the global table and disposed
		// the session, and left the entry here, so this list only ever
		// emptied when the document did. A consumer decoding one view in a
		// loop grew it by a handle per iteration for the life of the
		// document. Nothing could see it: viprs_acad__test_live_handles
		// counts the handle table, which is the one thing decode_close did
		// clean up.
		//
		// Same lock as Track and as Dispose, because Dispose walks this list
		// from whatever thread closed the document and a close racing a
		// decode_close is a documented shape, not a misuse.
		public void Untrack(IntPtr decode)
		{
			lock (_decodes)
			{
				_decodes.Remove(decode);
			}
		}

		// Closing a document releases every decode still open on it. The
		// alternative is a decode handle that outlives the thing it reads
		// from, and a consumer that then uses it is reading freed state with
		// no way to have known.
		public void Dispose()
		{
			_closed = true;
			IntPtr[] open;
			lock (_decodes)
			{
				open = _decodes.ToArray();
				_decodes.Clear();
			}

			foreach (IntPtr handle in open)
			{
				DecodeSession session = Handles.Remove<DecodeSession>(handle);
				if (session != null)
				{
					session.Dispose();
				}
			}

			Source.Dispose();
		}
	}

	internal sealed class DecodeSession : IDisposable
	{
		private readonly DocumentHandle _document;
		private readonly uint _viewIndex;
		private readonly IntPtr _cancelFlag;
		private readonly BatchWriter _writer;

		private ulong _items;
		private ulong _warnings;

		// The code this decode died of, or Result.Ok while it is still alive.
		//
		// Without it a refused decode could be asked again and would answer.
		// Stream() is an iterator, and an iterator whose body threw is
		// finished: every later MoveNext returns false. BatchWriter reads
		// that as "no more records", frames an empty batch with the
		// last-batch flag, reports done 1 and returns Ok. So the
		// grow-and-retry the header documents after a max_entities breach
		// handed the caller a well-formed, complete-looking stream missing
		// every record after the bound, with nothing to look at. That is
		// silent truncation, which is the one failure the limits section
		// promises cannot happen.
		//
		// Set once, never cleared. A decode is not a thing you recover.
		private uint _terminal;

		public DecodeSession(DocumentHandle document, uint viewIndex, IntPtr cancelFlag)
		{
			_document = document;
			_viewIndex = viewIndex;
			_cancelFlag = cancelFlag;
			_writer = new BatchWriter(Compose().GetEnumerator(), document.Limits);
		}

		public DocumentHandle Document
		{
			get { return _document; }
		}

		public unsafe uint NextBatch(byte* buf, ulong cap, out ulong written, out byte done)
		{
			written = 0ul;
			done = 0;

			// Whatever this decode died of, it says again, forever. Nothing
			// is written and done stays 0, so a caller's loop cannot read a
			// refusal as the end of a complete drawing.
			if (_terminal != Result.Ok)
			{
				return _terminal;
			}

			// Checked before any work, so a flag already set when the first
			// call arrives stops the decode without reading the document.
			if (IsCanceled())
			{
				_terminal = Result.Canceled;
				return _terminal;
			}

			// Not latched. The decode is fine; the document under it was
			// closed, which is the caller's ordering mistake and is reported
			// the same way every other bad handle is.
			if (_document.Closed)
			{
				return Result.InvalidArgument;
			}

			try
			{
				uint code = _writer.NextBatch(buf, cap, out written, out done);

				// BufferTooSmall is the one refusal that is not the decode's:
				// nothing was consumed, the caller grows its buffer and asks
				// again. Everything else that is not Ok ends it. Today that
				// is only max_output_bytes, which reaches here as a return
				// rather than a throw.
				if (code != Result.Ok && code != Result.BufferTooSmall)
				{
					_terminal = code;
					written = 0ul;
					done = 0;
				}

				return code;
			}
			catch (AbiException ex)
			{
				// max_entities, max_string_bytes, max_polyline_points,
				// max_block_depth and a cancel noticed inside the walk all
				// arrive here. The export turns this into the same code; what
				// it cannot do is remember it, because the export holds no
				// state and the next call is a new one.
				_terminal = ex.Code;
				written = 0ul;
				done = 0;
				throw;
			}
			catch (Exception)
			{
				// A bug, which the export reports as INTERNAL_ERROR. The
				// iterator behind the stream is just as dead as it is after a
				// coded failure, so the latch has to cover this too or the
				// call after a bug is the one that returns the truncated
				// stream.
				_terminal = Result.InternalError;
				written = 0ul;
				done = 0;
				throw;
			}
		}

		private unsafe bool IsCanceled()
		{
			if (_cancelFlag == IntPtr.Zero)
			{
				return false;
			}

			// Volatile, not a plain load. It happens once per call today, so
			// the compiler has no chance to hoist it, but the moment anything
			// reads it inside a loop a plain load may be read once and reused
			// for the rest of the decode. That turns a cancel into something
			// that arrives eventually or not at all, which is the worst
			// version of a cancel.
			return Volatile.Read(ref *(uint*)_cancelFlag.ToPointer()) != 0u;
		}

		// DocumentBegin, ViewBegin, the view's primitives, one forward probe,
		// ViewEnd, DocumentEnd. The probe is in every stream on purpose: it
		// makes a consumer's skip-the-unknown path something that runs on
		// every conformance run rather than something that runs the day a
		// second wire version exists.
		//
		// Internal rather than private so the fixture generator can dump what a
		// consumer actually receives instead of what the source produced. The
		// two used to be the same list and they are not any more: EMPTY_VIEW is
		// composed here, so a dump taken from the source below would be missing
		// the one record it is the evidence for.
		//
		// One walk per session. This is an iterator over instance counters, so
		// a second enumeration of the same DecodeSession counts everything
		// twice; the generator builds a fresh session for the dump.
		internal IEnumerable<Primitive> Compose()
		{
			SourceView view;
			if (!_document.Source.TryGetView((int)_viewIndex, out view))
			{
				throw new AbiException(Result.InvalidArgument, "no such view index");
			}

			yield return Primitive.DocumentBegin(
				(uint)_document.Source.ViewCount,
				_document.Source.DrawingVersion
			);

			yield return Primitive.ViewBegin(
				_viewIndex,
				view.Kind,
				view.MinX,
				view.MinY,
				view.MaxX,
				view.MaxY,
				view.ItemCount,
				view.Name
			);

			foreach (Primitive p in _document.Source.EnumerateView((int)_viewIndex, IsCanceled))
			{
				_items = _items + 1ul;
				if (_items > _document.Limits.MaxEntities)
				{
					throw new AbiException(
						Result.LimitExceeded,
						"this view holds more items than max_entities allows"
					);
				}

				if (p.Type == WireFormat.TypeWarning)
				{
					_warnings = _warnings + 1ul;
				}

				yield return p;
			}

			// A view that produced no geometry, said once, here rather than in
			// any source.
			//
			// It is the difference and not the count: the reader's
			// notifications head every view's stream whether or not the view
			// holds anything, so a drawing that could not be read at all
			// arrives with items and no geometry among them. _warnings is
			// already tracked for DocumentEnd, so geometry is _items minus it.
			//
			// Counted into _items and _warnings rather than yielded past them.
			// ViewEnd's record_count is derived from _items and this is a
			// record between ViewBegin and ViewEnd like any other, so a count
			// that skipped it would be wrong for exactly the views this fires
			// on. max_entities is deliberately not consulted: this record is
			// the shim's own and not an entity the drawing holds, and refusing
			// a view for being empty is not a bound anybody asked for.
			if (_items - _warnings == 0ul)
			{
				_items = _items + 1ul;
				_warnings = _warnings + 1ul;
				yield return Primitive.Warning(
					WarningCodes.EmptyView,
					0ul,
					"this view holds no geometry"
				);
			}

			yield return Primitive.ForwardProbe();

			// ViewBegin, the items, the probe, and this record itself.
			yield return Primitive.ViewEnd(_viewIndex, _items + 3ul);

			// Everything above plus DocumentBegin and this record.
			yield return Primitive.DocumentEnd(_items + 5ul, _warnings);
		}

		public void Dispose() { }
	}
}
