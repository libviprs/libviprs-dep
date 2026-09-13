using System;
using System.Collections.Generic;
using System.IO;
using ACadSharp;
using ACadSharp.IO;
using ACadSharp.Objects;
using ACadSharp.Tables;
using Viprs.Abi;
using Viprs.Cad;
using Viprs.Wire;

// The ACadSharp-backed document source.
//
// It does three things and refuses a fourth: it decides whether the six bytes
// at the front of the input are a version this build reads, it reads the
// document, and it hands the flattener the entities of one view. The fourth is
// resolving anything outside the input it was given. There is no fetch, no
// second open and no subprocess anywhere below this line, so an external
// reference crosses as a warning naming the path the drawing recorded.
namespace Viprs.Sources
{
	internal sealed class AcadSharpSource : IDocumentSource
	{
		// Interned at compile time, so reporting an out-of-memory refusal
		// costs no string.
		private const string OutOfMemoryMessage =
			"the reader ran out of memory reading this document";

		private CadDocument _document;
		private readonly ResolvedLimits _limits;
		private readonly List<string> _notifications = new List<string>();
		private readonly List<SourceView> _views = new List<SourceView>();
		private readonly List<BlockRecord> _blocks = new List<BlockRecord>();
		private uint _drawingVersion;
		private int _viewsWithoutExtents;

		// Volatile because two threads reach it. ABI.md says two handles may
		// be used from two threads and that closing a document releases every
		// decode open on it, so a close on one thread racing a decode on
		// another is a documented shape rather than a misuse. Every path
		// below is managed, so the worst outcome of losing the race was
		// always INTERNAL_ERROR and never a fault, but a plain bool lets a
		// reader keep a stale false for as long as it likes. Volatile turns
		// most of that window into the documented refusal instead.
		private volatile bool _closed;

		private AcadSharpSource(ResolvedLimits limits)
		{
			_limits = limits ?? ResolvedLimits.Defaults;
		}

		public IReadOnlyList<string> Notifications
		{
			get { return _notifications; }
		}

		// How many views reported the absent box because their extents were
		// not four finite numbers.
		//
		// Nothing on the boundary reads this and it is not on the wire. It
		// exists so the corpus can tell a substitution that ran from a drawing
		// whose extents were fine all along: once it works, every capture shows
		// four good numbers and the fixture carrying a NaN looks exactly like
		// every fixture that does not. A guard nobody can watch fire is a guard
		// that quietly stops firing.
		public int ViewsWithoutExtents
		{
			get { return _viewsWithoutExtents; }
		}

		public uint DrawingVersion
		{
			get { return _drawingVersion; }
		}

		// ------------------------------------------------------------ open

		// True when these bytes are a DWG this build reads. A DWG this build
		// does NOT read still matches, because an AC1009 file is a DWG and
		// telling its owner it is "not recognised" would be a lie: it is
		// recognised and refused, and those are different sentences.
		public static bool Matches(byte[] head)
		{
			return head != null && head.Length >= 6 && head[0] == (byte)'A' && head[1] == (byte)'C';
		}

		public const int MagicLength = 6;

		// The order here is the contract. max_input_bytes is applied to the
		// file's length before a byte of it is read, and the version
		// signature is read and judged before the reader is constructed, so
		// an unreadable version is UNSUPPORTED_FORMAT and never CORRUPT_INPUT
		// from somewhere deep in a parse.
		//
		// The bound itself is SourceFactory's, and applying it here as well
		// is what gave the same failure two different codes: this copy called
		// a failed stat INVALID_ARGUMENT and SourceFactory calls it
		// CORRUPT_INPUT. Nothing reaches this method except through
		// SourceFactory.OpenPath, which has already refused anything too big.
		public static AcadSharpSource OpenPath(string path, ResolvedLimits limits)
		{
			limits = limits ?? ResolvedLimits.Defaults;

			byte[] head = new byte[MagicLength];
			int read;
			try
			{
				using (FileStream fs = File.OpenRead(path))
				{
					read = Fill(fs, head);
				}
			}
			catch (Exception ex)
			{
				throw new AbiException(
					Result.CorruptInput,
					ex.GetType().Name + ": " + ex.Message
				);
			}

			uint version = Gate(head, read);
			AcadSharpSource source = new AcadSharpSource(limits);
			source.Read(() => File.OpenRead(path), version);
			return source;
		}

		// The caller owns the bytes for the duration of the call, so the
		// stream is a view over them and never a copy of them.
		public static AcadSharpSource OpenMemory(byte[] data, ResolvedLimits limits)
		{
			limits = limits ?? ResolvedLimits.Defaults;
			if (data == null)
			{
				throw new AbiException(Result.InvalidArgument, "data is null");
			}

			// As above: SourceFactory.OpenMemory applies max_input_bytes
			// before it decides which source these bytes belong to.
			uint version = Gate(data, data.Length < MagicLength ? data.Length : MagicLength);
			AcadSharpSource source = new AcadSharpSource(limits);
			source.Read(() => new MemoryStream(data, 0, data.Length, false, true), version);
			return source;
		}

		private static int Fill(Stream s, byte[] buf)
		{
			int total = 0;
			while (total < buf.Length)
			{
				int n = s.Read(buf, total, buf.Length - total);
				if (n <= 0)
				{
					break;
				}

				total += n;
			}

			return total;
		}

		private static uint Gate(byte[] head, int length)
		{
			uint version = VersionGate.Parse(head, length);
			if (!VersionGate.IsSupported(version))
			{
				throw new AbiException(
					Result.UnsupportedFormat,
					VersionGate.Describe(version)
						+ " is outside AC" + VersionGate.MinVersion
						+ " to AC" + VersionGate.MaxVersion
						+ ", which is what this build reads"
				);
			}

			return version;
		}

		private void Read(Func<Stream> open, uint version)
		{
			_drawingVersion = version;
			try
			{
				using (Stream stream = open())
				{
					_document = DwgReader.Read(stream, (sender, e) => OnNotification(e));
				}

				BuildViews();
			}
			catch (OutOfMemoryException)
			{
				// A constant, not a concatenation of the exception's type and
				// message. The one resource that is certainly gone on this
				// path is memory, and building a sentence out of three strings
				// to say so is asking the allocator for something at the exact
				// moment it has just refused. The exception object itself is
				// the smallest allocation that can carry a code at all, and
				// there is no version of this that needs none.
				throw new AbiException(Result.OutOfMemory, OutOfMemoryMessage);
			}
			catch (AbiException)
			{
				throw;
			}
			catch (Exception ex)
			{
				// Anything the reader throws on untrusted input is the
				// input's problem, not the caller's, and it is never an
				// abort: the process has to stay up for the next file.
				throw new AbiException(
					Result.CorruptInput,
					ex.GetType().FullName + ": " + ex.Message
				);
			}
		}

		private void OnNotification(NotificationEventArgs e)
		{
			// Every notification the reader raises becomes a Warning record.
			// None is dropped, which is the only way the stream can be
			// trusted to say what the reader could not do.
			string message = e.NotificationType + ": " + e.Message;
			if (e.Exception != null)
			{
				message = message + " (" + e.Exception.GetType().Name + ": " + e.Exception.Message + ")";
			}

			// Cut here rather than only at encode time, because this list is
			// built during the open and lives until the document is closed.
			// The reader's "Unlisted object with DXF name ..." message carries
			// the file's own class name, so a drawing that declares a 100 KB
			// one gets 100 KB of list for every copy of it before a single
			// record has been asked for, and no bound in
			// viprs_acad_limits_v1 has been consulted yet.
			//
			// The full sentence is still built above, because the reader hands
			// it over as a string and there is nothing to cut before it exists.
			// What this bounds is what stays.
			//
			// The encoder's own helper, so a message that survives being stored
			// is a message that fits on the wire.
			_notifications.Add(RecordEncoder.TruncateMessage(message, _limits.MaxStringBytes));
		}

		// ----------------------------------------------------------- views

		private void BuildViews()
		{
			List<Layout> layouts = new List<Layout>();
			foreach (Layout l in _document.Layouts)
			{
				layouts.Add(l);
			}

			// Model space first, then the layouts in their tab order. The
			// order is part of what a view index means, so it cannot be
			// whatever order the reader happened to build a dictionary in.
			layouts.Sort(
				(a, b) =>
				{
					bool am = IsModel(a);
					bool bm = IsModel(b);
					if (am != bm)
					{
						return am ? -1 : 1;
					}

					int byTab = a.TabOrder.CompareTo(b.TabOrder);
					return byTab != 0 ? byTab : string.CompareOrdinal(a.Name, b.Name);
				}
			);

			foreach (Layout l in layouts)
			{
				BlockRecord block = l.AssociatedBlock;
				bool usable = Finite(l.MinExtents.X)
					&& Finite(l.MinExtents.Y)
					&& Finite(l.MaxExtents.X)
					&& Finite(l.MaxExtents.Y);
				if (!usable)
				{
					_viewsWithoutExtents = _viewsWithoutExtents + 1;
				}

				_views.Add(
					new SourceView
					{
						Kind = IsModel(l) ? AbiConstants.ViewKindModel : AbiConstants.ViewKindLayout,
						MinX = usable ? l.MinExtents.X : AbsentMin,
						MinY = usable ? l.MinExtents.Y : AbsentMin,
						MaxX = usable ? l.MaxExtents.X : AbsentMax,
						MaxY = usable ? l.MaxExtents.Y : AbsentMax,
						ItemCount = block == null ? 0ul : (ulong)block.Entities.Count,
						Name = l.Name ?? string.Empty,
					}
				);
				_blocks.Add(block);
			}
		}

		// What a view reports when its extents are not four numbers.
		//
		// docs/WIRE.md's finiteness guarantee covers records 3 to 10 and leaves
		// ViewBegin out on purpose: its extents are a bounding box the source
		// reports rather than a shape anybody draws, and a view holding nothing
		// has no finite one, so promising a number there would mean inventing
		// one. That reasoning is right and it left a hole underneath it. A
		// layout's extents are four doubles a file holds, so a drawing can hand
		// this shim a NaN, and g13_bad_extents.dwg is a drawing that does. One
		// NaN in a bounding box makes every comparison against it false, so the
		// box is NaN in every direction by the time anything has used it, and
		// the consumer has nothing it could have compared against to find out.
		//
		// The inverted box is the answer, and it is not invented: this is the
		// pair AutoCAD itself writes into EXTMIN and EXTMAX for a drawing with
		// nothing in it, so a consumer that already understands one understands
		// the other. `min_x > max_x` is then a comparison that works and means
		// "this view has no usable extents".
		//
		// What it does not do is separate a view that is empty from a drawing
		// that is damaged: both come out as this. Telling those apart wants a
		// warning code of its own, and a code wants a row in docs/WIRE.md.
		private const double AbsentMin = 1e20;
		private const double AbsentMax = -1e20;

		private static bool Finite(double v)
		{
			return !double.IsNaN(v) && !double.IsInfinity(v);
		}

		private static bool IsModel(Layout l)
		{
			return string.Equals(l.Name, Layout.ModelLayoutName, StringComparison.Ordinal);
		}

		public int ViewCount
		{
			get { return _views.Count; }
		}

		public bool TryGetView(int index, out SourceView view)
		{
			if (index < 0 || index >= _views.Count)
			{
				view = default(SourceView);
				return false;
			}

			view = _views[index];
			return true;
		}

		// ---------------------------------------------------------- decode

		public IEnumerable<Primitive> EnumerateView(int index, Func<bool> canceled)
		{
			if (index < 0 || index >= _views.Count)
			{
				throw new AbiException(Result.InvalidArgument, "no such view index");
			}

			return Stream(index, canceled);
		}

		private IEnumerable<Primitive> Stream(int index, Func<bool> canceled)
		{
			// The reader's notifications are about the document, not about
			// one view, so they head every view's stream. A consumer decoding
			// only the second layout still has to be told what the reader
			// could not read, and the alternative is a warning that reaches
			// nobody.
			foreach (string n in _notifications)
			{
				yield return Flattener.ReaderNotification(n);
			}

			BlockRecord block = _blocks[index];
			if (block == null)
			{
				yield break;
			}

			Flattener flattener = new Flattener(_limits);
			foreach (Primitive p in flattener.Walk(block.Entities, canceled))
			{
				yield return p;
			}
		}

		public void Dispose()
		{
			if (_closed)
			{
				return;
			}

			_closed = true;
			_document = null;
			_views.Clear();
			_blocks.Clear();
		}
	}
}
