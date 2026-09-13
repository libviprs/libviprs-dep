using System;
using System.Collections.Generic;
using Viprs.Wire;

// The seam between "what a drawing is" and "what the boundary is".
//
// Everything above this interface (the exports, the limits, the batch writer,
// the wire format) is written once and does not change when a new kind of
// document shows up. Everything below it knows about file formats and knows
// nothing about the ABI. The synthetic source on this issue and the real
// adapter on the next one are two implementations of the same six members,
// and SourceFactory is the only file that has to name both.
//
// Opening is on the factory rather than here, because a source that has to be
// constructed before it can say whether it recognises the input is the wrong
// shape: the factory looks at the bytes and picks.
namespace Viprs.Sources
{
	// One view's metadata, as the source knows it. It becomes both
	// viprs_acad_view_info_v1 and the ViewBegin record, which is why the name is
	// a string here and a length-prefixed byte run in both of those.
	internal struct SourceView
	{
		public uint Kind;
		public double MinX;
		public double MinY;
		public double MaxX;
		public double MaxY;
		public ulong ItemCount;
		public string Name;
	}

	internal interface IDocumentSource : IDisposable
	{
		// The numeric AC10xx code the document was read from, or 0 when the
		// source has no meaningful answer.
		uint DrawingVersion { get; }

		int ViewCount { get; }

		// False for an index out of range, which the export turns into
		// VIPRS_ACAD_INVALID_ARGUMENT. Never throws for a bad index.
		bool TryGetView(int index, out SourceView view);

		// Flattened primitives for one view, in stream order. Lazy on
		// purpose: a batch is filled by pulling from this, so a decode that
		// is cancelled or hits a limit stops reading the document rather
		// than finishing the work and throwing it away.
		//
		// `canceled` is the decode's cancel flag, and a source that can spend
		// real time between two primitives has to poll it. Reading the flag
		// only between calls to decode_next_batch is not enough: a document
		// can make a source do unbounded work without yielding anything (a
		// chain of block records each holding several insertions of the next
		// expands exponentially and emits nothing), and while that runs the
		// export never returns to look at the flag. A source that always
		// yields promptly may ignore it. Null means never cancelled.
		IEnumerable<Primitive> EnumerateView(int index, Func<bool> canceled);
	}
}
