using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using Viprs.Cad;
using Viprs.Cad.Sources;

namespace Viprs.Cad.Fixtures;

// The scenario runner the corpus is measured with.
//
// One scenario per process, always. Peak RSS is a process-wide number, and two
// scenarios in one process measure each other: the second one inherits
// whatever the first one committed. That is also why the peak counter is reset
// through /proc/self/clear_refs between opening a document and decoding it,
// because otherwise "peak during the decode" is really "peak during the read",
// which is the number the streaming claim is not about.
public static class Harness
{
	public const int DefaultBatchBytes = 64 * 1024;

	private sealed class Options
	{
		public string Path;
		public int View;
		public bool Memory;
		public int BatchBytes = DefaultBatchBytes;
		public ulong MaxInput;
		public ulong MaxEntities;
		public ulong MaxString;
		public ulong MaxPolyline;
		public uint MaxDepth;
		public ulong MaxOutput;
		public int CancelAfter = -1;
		public string DumpPath;
		public string CheckPath;
		public bool NoDecode;
	}

	// ------------------------------------------------------------- /proc

	private static long ReadStatus(string key)
	{
		try
		{
			foreach (string line in File.ReadLines("/proc/self/status"))
			{
				if (line.StartsWith(key, StringComparison.Ordinal))
				{
					string[] parts = line.Split(':');
					string v = parts[1].Trim().Replace(" kB", string.Empty);
					return long.Parse(v, CultureInfo.InvariantCulture);
				}
			}
		}
		catch (Exception)
		{
		}

		return -1;
	}

	private static long Rss()
	{
		return ReadStatus("VmRSS:");
	}

	private static long PeakRss()
	{
		return ReadStatus("VmHWM:");
	}

	// Linux resets VmHWM to the current VmRSS when 5 is written here. Without
	// it the peak is whatever the document read committed and the decode's own
	// growth is invisible underneath it.
	private static bool ResetPeak()
	{
		try
		{
			File.WriteAllText("/proc/self/clear_refs", "5\n");
			return true;
		}
		catch (Exception)
		{
			return false;
		}
	}

	// -------------------------------------------------------------- json

	private sealed class Json
	{
		private readonly StringBuilder _sb = new StringBuilder();
		private bool _first = true;

		public Json()
		{
			this._sb.Append('{');
		}

		private void Comma()
		{
			if (!this._first)
			{
				this._sb.Append(',');
			}
			this._first = false;
			this._sb.Append('\n');
		}

		public Json Str(string k, string v)
		{
			this.Comma();
			this._sb.Append("  ").Append(Quote(k)).Append(": ")
				.Append(v is null ? "null" : Quote(v));
			return this;
		}

		public Json Num(string k, long v)
		{
			this.Comma();
			this._sb.Append("  ").Append(Quote(k)).Append(": ")
				.Append(v.ToString(CultureInfo.InvariantCulture));
			return this;
		}

		public Json UNum(string k, ulong v)
		{
			this.Comma();
			this._sb.Append("  ").Append(Quote(k)).Append(": ")
				.Append(v.ToString(CultureInfo.InvariantCulture));
			return this;
		}

		public Json Bool(string k, bool v)
		{
			this.Comma();
			this._sb.Append("  ").Append(Quote(k)).Append(": ").Append(v ? "true" : "false");
			return this;
		}

		public Json Strings(string k, IList<string> values)
		{
			this.Comma();
			this._sb.Append("  ").Append(Quote(k)).Append(": [");
			for (int i = 0; i < values.Count; i++)
			{
				if (i > 0)
				{
					this._sb.Append(", ");
				}
				this._sb.Append(Quote(values[i]));
			}
			this._sb.Append(']');
			return this;
		}

		public override string ToString()
		{
			return this._sb.ToString() + "\n}";
		}

		public static string Quote(string s)
		{
			StringBuilder sb = new StringBuilder();
			sb.Append('"');
			foreach (char c in s ?? string.Empty)
			{
				switch (c)
				{
					case '"': sb.Append("\\\""); break;
					case '\\': sb.Append("\\\\"); break;
					case '\n': sb.Append("\\n"); break;
					case '\r': sb.Append("\\r"); break;
					case '\t': sb.Append("\\t"); break;
					default:
						if (c < 0x20 || c > 0x7E)
						{
							sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
						}
						else
						{
							sb.Append(c);
						}
						break;
				}
			}
			sb.Append('"');
			return sb.ToString();
		}
	}

	// ------------------------------------------------------------ decode

	public static int Decode(string[] args)
	{
		Options o = Parse(args, out string parseError);
		if (parseError != null)
		{
			Console.Error.WriteLine(parseError);
			return 2;
		}

		long rssStart = Rss();
		long fileBytes = -1;
		try
		{
			fileBytes = new FileInfo(o.Path).Length;
		}
		catch (Exception)
		{
		}

		AdapterLimits limits = AdapterLimits.FromFields(
			o.MaxInput, o.MaxEntities, o.MaxString, o.MaxPolyline, o.MaxDepth, o.MaxOutput);

		Json json = new Json();
		json.Str("fixture", Path.GetFileName(o.Path));
		json.Bool("memory", o.Memory);
		json.Num("file_bytes", fileBytes);
		json.Num("batch_bytes", o.BatchBytes);
		json.Str("limits", limits.ToString());

		uint code;
		string detail;
		IDocumentSource source = null;
		byte[] buffer = null;

		if (o.Memory)
		{
			// The caller's copy, which is exactly what open_memory costs and
			// open_path_utf8 does not.
			buffer = File.ReadAllBytes(o.Path);
			code = SourceFactory.OpenMemory(buffer, limits, out source, out detail);
		}
		else
		{
			code = SourceFactory.OpenPath(o.Path, limits, out source, out detail);
		}

		json.Str("open_code", AdapterResult.Name(code));
		json.Str("open_detail", detail);
		json.Num("rss_start_kb", rssStart);
		json.Num("rss_after_open_kb", Rss());

		if (code != AdapterResult.Ok)
		{
			SourceFactory.Close(source);
			json.Num("live_handles", HandleRegistry.Live);
			json.Num("peak_rss_kb", PeakRss());
			json.Str("decode_code", "NOT_REACHED");
			Console.Out.Write(json.ToString());
			Console.Out.Write("\n");
			return 0;
		}

		AcadSharpSource acad = source as AcadSharpSource;
		List<string> notes = new List<string>(acad is null ? new List<string>() : acad.Notifications);
		json.Num("view_count", source.ViewCount);
		json.Num("notification_count", notes.Count);
		json.Strings("notifications", notes);

		if (o.NoDecode)
		{
			SourceFactory.Close(source);
			json.Num("live_handles", HandleRegistry.Live);
			json.Num("peak_rss_kb", PeakRss());
			json.Str("decode_code", "SKIPPED");
			Console.Out.Write(json.ToString());
			Console.Out.Write("\n");
			return 0;
		}

		MutableCancelFlag cancel = new MutableCancelFlag();
		List<string> dump = new List<string>();
		int index = 0;
		ulong bytes = 0;
		ulong records = 0;
		ulong batches = 0;
		string bound = null;
		string failure = null;
		uint decodeCode = AdapterResult.Ok;

		// The record stream is pulled twice only when a dump is asked for,
		// and the dump walk is a separate decode over the same document so
		// the measured decode is never carrying a list of strings.
		IEnumerable<Record> stream = source.Decode(o.View, limits, cancel);
		DecodeSession session = new DecodeSession(stream, limits, cancel);
		byte[] buf = new byte[o.BatchBytes];

		// Settle the heap before the baseline, so "RSS after decode_begin" is
		// a number about the document and not about whatever the read left
		// uncollected. The same forced collection runs again at the end, and
		// the difference between the two is retention: the question the
		// streaming claim is actually about, which peak RSS answers only
		// indirectly because peak RSS also counts garbage.
		long managedAfterBegin = GC.GetTotalMemory(true) / 1024;
		long rssAfterBegin = Rss();
		bool peakReset = ResetPeak();

		int grownTo = buf.Length;
		while (true)
		{
			uint r = session.NextBatch(buf, 0, buf.Length, out int written, out bool done);

			// LIMIT_EXCEEDED means two different things and the header says
			// how to tell them apart: a buffer too small for the next batch
			// writes the needed size through *written, and a bound that was
			// actually breached writes zero. A real consumer grows and
			// retries, so the harness does too rather than recording a
			// refusal the ABI did not make.
			if (r == AdapterResult.LimitExceeded && written > buf.Length)
			{
				buf = new byte[written];
				grownTo = written;
				continue;
			}

			if (r != AdapterResult.Ok)
			{
				decodeCode = r;
				bound = session.FailedBound;
				failure = session.FailureMessage;
				break;
			}

			bytes += (ulong)written;
			batches++;
			records = session.RecordCount;

			if (done)
			{
				break;
			}

			if (o.CancelAfter >= 0 && (int)batches == o.CancelAfter)
			{
				// Set between two decode_next_batch calls, which is the only
				// place the header says it is read.
				cancel.Set();
			}
		}

		long peakDuringDecode = PeakRss();
		long managedAfterDecode = GC.GetTotalMemory(true) / 1024;
		long rssAfterCollect = Rss();
		session.Dispose();

		json.Str("decode_code", AdapterResult.Name(decodeCode));
		json.Str("decode_bound", bound);
		json.Str("decode_detail", failure);
		json.UNum("records", records);
		json.UNum("batches", batches);
		json.UNum("output_bytes", bytes);
		json.Num("grown_batch_bytes", grownTo);
		json.Num("rss_after_begin_kb", rssAfterBegin);
		json.Bool("peak_reset", peakReset);
		json.Num("rss_peak_during_decode_kb", peakDuringDecode);
		json.Num("rss_decode_growth_kb", peakDuringDecode - rssAfterBegin);
		json.Num("managed_after_begin_kb", managedAfterBegin);
		json.Num("managed_after_decode_kb", managedAfterDecode);
		json.Num("managed_retained_kb", managedAfterDecode - managedAfterBegin);
		json.Num("rss_after_collect_kb", rssAfterCollect);

		if (o.DumpPath != null || o.CheckPath != null)
		{
			foreach (Record rec in source.Decode(o.View, limits, NeverCanceled.Instance))
			{
				dump.Add(CanonicalDump.Line(index, rec));
				index++;
			}

			if (o.DumpPath != null)
			{
				File.WriteAllText(o.DumpPath, string.Join("\n", dump) + "\n");
			}

			if (o.CheckPath != null)
			{
				string[] expected = File.Exists(o.CheckPath)
					? File.ReadAllLines(o.CheckPath)
					: Array.Empty<string>();
				string diff = CanonicalDump.FirstDifference(expected, dump.ToArray());
				json.Str("check", diff is null ? "identical" : diff);
			}

			json.Num("dumped_records", dump.Count);
		}

		SourceFactory.Close(source);
		GC.KeepAlive(buffer);

		json.Num("live_handles", HandleRegistry.Live);
		json.Num("peak_rss_kb", PeakRss());
		Console.Out.Write(json.ToString());
		Console.Out.Write("\n");
		return 0;
	}

	private static Options Parse(string[] args, out string error)
	{
		error = null;
		Options o = new Options();
		if (args.Length < 1)
		{
			error = "decode needs a path";
			return o;
		}

		o.Path = args[0];
		for (int i = 1; i < args.Length; i++)
		{
			string a = args[i];
			switch (a)
			{
				case "--memory": o.Memory = true; break;
				case "--no-decode": o.NoDecode = true; break;
				case "--view": o.View = int.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--batch": o.BatchBytes = int.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--max-input": o.MaxInput = ulong.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--max-entities": o.MaxEntities = ulong.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--max-string": o.MaxString = ulong.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--max-polyline": o.MaxPolyline = ulong.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--max-depth": o.MaxDepth = uint.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--max-output": o.MaxOutput = ulong.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--cancel-after": o.CancelAfter = int.Parse(args[++i], CultureInfo.InvariantCulture); break;
				case "--dump": o.DumpPath = args[++i]; break;
				case "--check": o.CheckPath = args[++i]; break;
				default:
					error = "unknown option " + a;
					return o;
			}
		}

		return o;
	}
}
