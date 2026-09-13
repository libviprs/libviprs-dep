using System;
using System.Collections.Generic;
using System.IO;
using ACadSharp;
using ACadSharp.Entities;
using ACadSharp.IO;
using ACadSharp.Objects;
using ACadSharp.Tables;

namespace Viprs.Cad.Sources;

// The ACadSharp-backed document source.
//
// It does three things and refuses to do a fourth: it decides whether the six
// bytes at the front of the file are a version this build reads, it reads the
// document, and it hands the flattener the entities of one view. The fourth is
// resolving anything outside the file it was given. There is no fetch, no
// second open and no subprocess anywhere below this line, so an XREF crosses
// as a warning naming the path the drawing recorded.
public sealed class AcadSharpSource : IDocumentSource
{
	private CadDocument _document;
	private readonly List<string> _notifications = new List<string>();
	private readonly List<ViewInfo> _views = new List<ViewInfo>();
	private readonly List<BlockRecord> _blocks = new List<BlockRecord>();
	private bool _closed;

	public string FailureDetail { get; private set; }

	public IReadOnlyList<string> Notifications
	{
		get { return this._notifications; }
	}

	private AcadSharpSource()
	{
	}

	// ---------------------------------------------------------------- open

	// viprs_acad_open_path_utf8. The order here is the contract: the size
	// bound is applied to the file's length before a byte is read, and the
	// version signature is read and judged before the reader is constructed.
	public static uint OpenPath(string path, AdapterLimits limits, out AcadSharpSource source, out string detail)
	{
		source = null;
		detail = null;
		limits = limits ?? AdapterLimits.Defaults();

		if (string.IsNullOrEmpty(path))
		{
			detail = "path is empty";
			return AdapterResult.InvalidArgument;
		}

		long length;
		try
		{
			FileInfo info = new FileInfo(path);
			if (!info.Exists)
			{
				detail = "no file at the given path";
				return AdapterResult.InvalidArgument;
			}

			length = info.Length;
		}
		catch (Exception ex)
		{
			detail = ex.GetType().Name + ": " + ex.Message;
			return AdapterResult.InvalidArgument;
		}

		if ((ulong)length > limits.MaxInputBytes)
		{
			detail = "the file is " + length + " bytes and max_input_bytes is " + limits.MaxInputBytes;
			return AdapterResult.LimitExceeded;
		}

		byte[] head = new byte[6];
		int read;
		try
		{
			using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
			{
				read = Fill(fs, head);
			}
		}
		catch (Exception ex)
		{
			detail = ex.GetType().Name + ": " + ex.Message;
			return AdapterResult.CorruptInput;
		}

		uint gate = Gate(head, read, out detail);
		if (gate != AdapterResult.Ok)
		{
			return gate;
		}

		return Build(() => new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read),
			out source, ref detail);
	}

	// viprs_acad_open_memory. The caller owns the bytes for the duration of
	// the call, so the stream is a view over them and never a copy.
	public static uint OpenMemory(byte[] data, AdapterLimits limits, out AcadSharpSource source, out string detail)
	{
		source = null;
		detail = null;
		limits = limits ?? AdapterLimits.Defaults();

		if (data is null)
		{
			detail = "data is null";
			return AdapterResult.InvalidArgument;
		}

		if ((ulong)data.LongLength > limits.MaxInputBytes)
		{
			detail = "the buffer is " + data.LongLength + " bytes and max_input_bytes is " + limits.MaxInputBytes;
			return AdapterResult.LimitExceeded;
		}

		uint gate = Gate(data, data.Length < 6 ? data.Length : 6, out detail);
		if (gate != AdapterResult.Ok)
		{
			return gate;
		}

		return Build(() => new MemoryStream(data, 0, data.Length, false, true), out source, ref detail);
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

	private static uint Gate(byte[] head, int length, out string detail)
	{
		uint version = VersionGate.Parse(head, length);
		if (!VersionGate.IsSupported(version))
		{
			detail = VersionGate.Describe(version)
				+ " is outside AC" + VersionGate.MinVersion + " to AC" + VersionGate.MaxVersion
				+ ", which is what this build reads";
			return AdapterResult.UnsupportedFormat;
		}

		detail = null;
		return AdapterResult.Ok;
	}

	private static uint Build(Func<Stream> open, out AcadSharpSource source, ref string detail)
	{
		source = null;
		AcadSharpSource s = new AcadSharpSource();
		try
		{
			using (Stream stream = open())
			{
				s._document = DwgReader.Read(stream, (sender, e) => s.OnNotification(e));
			}
		}
		catch (OutOfMemoryException ex)
		{
			detail = ex.GetType().Name + ": " + ex.Message;
			return AdapterResult.OutOfMemory;
		}
		catch (Exception ex)
		{
			// Anything the reader throws on untrusted input is the input's
			// problem, not the caller's, and it is never an abort: the
			// process has to stay up for the next file.
			detail = ex.GetType().FullName + ": " + ex.Message;
			return AdapterResult.CorruptInput;
		}

		try
		{
			s.BuildViews();
		}
		catch (Exception ex)
		{
			detail = ex.GetType().FullName + ": " + ex.Message;
			return AdapterResult.CorruptInput;
		}

		HandleRegistry.DocumentOpened();
		source = s;
		return AdapterResult.Ok;
	}

	private void OnNotification(NotificationEventArgs e)
	{
		// Every notification the reader raises becomes a Warning record.
		// None is dropped, which is the only way the stream can be trusted to
		// say what the reader could not do.
		string message = e.NotificationType + ": " + e.Message;
		if (e.Exception != null)
		{
			message += " (" + e.Exception.GetType().Name + ": " + e.Exception.Message + ")";
		}

		this._notifications.Add(message);
	}

	// --------------------------------------------------------------- views

	private void BuildViews()
	{
		List<Layout> layouts = new List<Layout>();
		foreach (Layout l in this._document.Layouts)
		{
			layouts.Add(l);
		}

		layouts.Sort((a, b) =>
		{
			bool am = IsModel(a);
			bool bm = IsModel(b);
			if (am != bm)
			{
				return am ? -1 : 1;
			}

			int byTab = a.TabOrder.CompareTo(b.TabOrder);
			return byTab != 0 ? byTab : string.CompareOrdinal(a.Name, b.Name);
		});

		uint index = 0;
		foreach (Layout l in layouts)
		{
			BlockRecord block = l.AssociatedBlock;
			ViewInfo v = new ViewInfo
			{
				Index = index,
				Kind = IsModel(l) ? ViewInfo.KindModel : ViewInfo.KindLayout,
				MinX = l.MinExtents.X,
				MinY = l.MinExtents.Y,
				MaxX = l.MaxExtents.X,
				MaxY = l.MaxExtents.Y,
				EntityCount = block is null ? 0UL : (ulong)block.Entities.Count,
				Name = l.Name ?? string.Empty,
			};

			this._views.Add(v);
			this._blocks.Add(block);
			index++;
		}
	}

	private static bool IsModel(Layout l)
	{
		return string.Equals(l.Name, Layout.ModelLayoutName, StringComparison.Ordinal);
	}

	public int ViewCount
	{
		get { return this._views.Count; }
	}

	public ViewInfo GetView(int index)
	{
		if (index < 0 || index >= this._views.Count)
		{
			return null;
		}

		return this._views[index];
	}

	// -------------------------------------------------------------- decode

	public IEnumerable<Record> Decode(int viewIndex, AdapterLimits limits, ICancelFlag cancel)
	{
		if (viewIndex < 0 || viewIndex >= this._views.Count)
		{
			throw new ArgumentOutOfRangeException(nameof(viewIndex));
		}

		return this.Stream(viewIndex, limits ?? AdapterLimits.Defaults());
	}

	private IEnumerable<Record> Stream(int viewIndex, AdapterLimits limits)
	{
		// The reader's notifications are about the document, not about one
		// view, so they head every view's stream. A consumer decoding only
		// the second layout still has to be told what the reader could not
		// read, and the alternative is a warning that reaches nobody.
		foreach (string n in this._notifications)
		{
			yield return Flattener.ReaderNotification(n);
		}

		BlockRecord block = this._blocks[viewIndex];
		if (block is null)
		{
			yield break;
		}

		Flattener flattener = new Flattener(limits);
		foreach (Record r in flattener.Walk(block.Entities))
		{
			yield return r;
		}
	}

	public void Dispose()
	{
		if (this._closed)
		{
			return;
		}

		this._closed = true;
		this._document = null;
		this._views.Clear();
		this._blocks.Clear();
		HandleRegistry.DocumentClosed();
	}
}
