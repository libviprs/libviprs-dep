using System;
using System.Collections.Generic;
using Viprs.Abi;
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

		public DecodeSession(DocumentHandle document, uint viewIndex, IntPtr cancelFlag)
		{
			_document = document;
			_viewIndex = viewIndex;
			_cancelFlag = cancelFlag;
			_writer = new BatchWriter(Stream().GetEnumerator(), document.Limits);
		}

		public DocumentHandle Document
		{
			get { return _document; }
		}

		public unsafe uint NextBatch(byte* buf, ulong cap, out ulong written, out byte done)
		{
			written = 0ul;
			done = 0;

			// Checked before any work, so a flag already set when the first
			// call arrives stops the decode without reading the document.
			if (IsCanceled())
			{
				return Result.Canceled;
			}

			if (_document.Closed)
			{
				return Result.InvalidArgument;
			}

			return _writer.NextBatch(buf, cap, out written, out done);
		}

		private unsafe bool IsCanceled()
		{
			if (_cancelFlag == IntPtr.Zero)
			{
				return false;
			}

			return *(uint*)_cancelFlag.ToPointer() != 0u;
		}

		// DocumentBegin, ViewBegin, the view's primitives, one forward probe,
		// ViewEnd, DocumentEnd. The probe is in every stream on purpose: it
		// makes a consumer's skip-the-unknown path something that runs on
		// every conformance run rather than something that runs the day a
		// second wire version exists.
		private IEnumerable<Primitive> Stream()
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

			foreach (Primitive p in _document.Source.EnumerateView((int)_viewIndex))
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

			yield return Primitive.ForwardProbe();

			// ViewBegin, the items, the probe, and this record itself.
			yield return Primitive.ViewEnd(_viewIndex, _items + 3ul);

			// Everything above plus DocumentBegin and this record.
			yield return Primitive.DocumentEnd(_items + 5ul, _warnings);
		}

		public void Dispose() { }
	}
}
