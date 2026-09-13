using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Viprs.Abi;
using Viprs.Cad;
using Viprs.Sources;
using Viprs.Wire;

// The scenario runner the corpus is measured with.
//
// It drives the real pipeline rather than a copy of it: SourceFactory picks
// the source, DocumentHandle and DecodeSession compose the stream, BatchWriter
// frames it, and handles go through the same Handles table the exports use.
// The exports themselves are [UnmanagedCallersOnly] and cannot be called from
// managed code, so this mirrors what they do with handles; everything under
// them is the same code the shared library runs.
//
// One scenario per process, always. Peak RSS is a process-wide number, and two
// scenarios in one process measure each other: the second inherits whatever
// the first committed. That is also why the peak counter is reset through
// /proc/self/clear_refs between opening a document and decoding it, because
// otherwise "peak during the decode" is really "peak during the read", which
// is the number the streaming claim is not about.
namespace Viprs.Cad.Fixtures
{
	internal static class Harness
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

		// -------------------------------------------------------- /proc

		private static long ReadStatus(string key)
		{
			try
			{
				foreach (string line in File.ReadLines("/proc/self/status"))
				{
					if (line.StartsWith(key, StringComparison.Ordinal))
					{
						string[] parts = line.Split(':');
						return long.Parse(
							parts[1].Trim().Replace(" kB", string.Empty),
							CultureInfo.InvariantCulture
						);
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

		// Linux resets VmHWM to the current VmRSS when 5 is written here.
		// Without it the peak is whatever the document read committed and the
		// decode's own growth is invisible underneath it.
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

		// --------------------------------------------------------- json

		private sealed class Json
		{
			private readonly StringBuilder _sb = new StringBuilder();
			private bool _first = true;

			public Json()
			{
				_sb.Append('{');
			}

			private void Comma()
			{
				if (!_first)
				{
					_sb.Append(',');
				}

				_first = false;
				_sb.Append('\n');
			}

			public Json Str(string k, string v)
			{
				Comma();
				_sb.Append("  ").Append(Quote(k)).Append(": ")
					.Append(v == null ? "null" : Quote(v));
				return this;
			}

			public Json Num(string k, long v)
			{
				Comma();
				_sb.Append("  ").Append(Quote(k)).Append(": ")
					.Append(v.ToString(CultureInfo.InvariantCulture));
				return this;
			}

			public Json UNum(string k, ulong v)
			{
				Comma();
				_sb.Append("  ").Append(Quote(k)).Append(": ")
					.Append(v.ToString(CultureInfo.InvariantCulture));
				return this;
			}

			public Json Bool(string k, bool v)
			{
				Comma();
				_sb.Append("  ").Append(Quote(k)).Append(": ").Append(v ? "true" : "false");
				return this;
			}

			public Json Strings(string k, IList<string> values)
			{
				Comma();
				_sb.Append("  ").Append(Quote(k)).Append(": [");
				for (int i = 0; i < values.Count; i++)
				{
					if (i > 0)
					{
						_sb.Append(", ");
					}

					_sb.Append(Quote(values[i]));
				}

				_sb.Append(']');
				return this;
			}

			public override string ToString()
			{
				return _sb.ToString() + "\n}";
			}

			public static string Quote(string s)
			{
				StringBuilder sb = new StringBuilder();
				sb.Append('"');
				foreach (char c in s ?? string.Empty)
				{
					switch (c)
					{
						case '"':
							sb.Append("\\\"");
							break;
						case '\\':
							sb.Append("\\\\");
							break;
						case '\n':
							sb.Append("\\n");
							break;
						case '\r':
							sb.Append("\\r");
							break;
						case '\t':
							sb.Append("\\t");
							break;
						default:
							if (c < 0x20 || c > 0x7E)
							{
								sb.Append("\\u")
									.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
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

		// ------------------------------------------------------- decode

		public static unsafe int Decode(string[] args)
		{
			string parseError;
			Options o = Parse(args, out parseError);
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

			ResolvedLimits limits = Limits(o);

			Json json = new Json();
			json.Str("fixture", Path.GetFileName(o.Path));
			json.Bool("memory", o.Memory);
			json.Num("file_bytes", fileBytes);
			json.Num("batch_bytes", o.BatchBytes);
			json.Str(
				"limits",
				string.Format(
					CultureInfo.InvariantCulture,
					"input={0} entities={1} string={2} polyline={3} depth={4} output={5}",
					limits.MaxInputBytes,
					limits.MaxEntities,
					limits.MaxStringBytes,
					limits.MaxPolylinePoints,
					limits.MaxBlockDepth,
					limits.MaxOutputBytes
				)
			);

			IDocumentSource source = null;
			byte[] buffer = null;
			uint openCode = Result.Ok;
			string openDetail = null;

			try
			{
				if (o.Memory)
				{
					// The caller's copy, which is exactly what open_memory
					// costs and open_path_utf8 does not.
					buffer = File.ReadAllBytes(o.Path);
					source = SourceFactory.OpenMemory(buffer, limits);
				}
				else
				{
					source = SourceFactory.OpenPath(o.Path, limits);
				}
			}
			catch (AbiException ex)
			{
				openCode = ex.Code;
				openDetail = ex.Message;
			}
			catch (Exception ex)
			{
				openCode = Result.InternalError;
				openDetail = ex.GetType().FullName + ": " + ex.Message;
			}

			json.Str("open_code", Name(openCode));
			json.Str("open_detail", openDetail);
			json.Num("rss_start_kb", rssStart);
			json.Num("rss_after_open_kb", Rss());

			if (openCode != Result.Ok)
			{
				json.Num("live_handles", Handles.LiveCount);
				json.Num("peak_rss_kb", PeakRss());
				json.Str("decode_code", "NOT_REACHED");
				Emit(json);
				return 0;
			}

			// The same handle discipline the exports use: a document handle
			// in the table, every decode tracked by it, and closing the
			// document closing them all.
			IntPtr documentHandle = Handles.Add(new DocumentHandle(source, limits));
			DocumentHandle document = Handles.Get<DocumentHandle>(documentHandle);

			AcadSharpSource acad = source as AcadSharpSource;
			List<string> notes = new List<string>();
			if (acad != null)
			{
				notes.AddRange(acad.Notifications);
			}

			json.Num("view_count", source.ViewCount);
			json.Num("notification_count", notes.Count);
			json.Strings("notifications", notes);

			if (o.NoDecode)
			{
				CloseDocument(documentHandle);
				json.Num("live_handles", Handles.LiveCount);
				json.Num("peak_rss_kb", PeakRss());
				json.Str("decode_code", "SKIPPED");
				Emit(json);
				return 0;
			}

			IntPtr cancelFlag = Marshal.AllocHGlobal(sizeof(uint));
			*(uint*)cancelFlag.ToPointer() = 0u;

			DecodeSession session = new DecodeSession(document, (uint)o.View, cancelFlag);
			IntPtr decodeHandle = Handles.Add(session);
			document.Track(decodeHandle);

			byte[] buf = new byte[o.BatchBytes];
			int grownTo = buf.Length;
			ulong bytes = 0ul;
			ulong batches = 0ul;
			uint decodeCode = Result.Ok;
			string decodeDetail = null;

			// Settle the heap before the baseline, so "RSS after decode_begin"
			// is a number about the document and not about whatever the read
			// left uncollected. The same forced collection runs at the end,
			// and the difference between the two is retention: the question
			// the streaming claim is actually about, which peak RSS answers
			// only indirectly because peak RSS also counts garbage.
			long managedAfterBegin = GC.GetTotalMemory(true) / 1024;
			long rssAfterBegin = Rss();
			bool peakReset = ResetPeak();

			while (true)
			{
				ulong written;
				byte done;
				uint r;
				try
				{
					fixed (byte* p = buf)
					{
						r = session.NextBatch(p, (ulong)buf.Length, out written, out done);
					}
				}
				catch (AbiException ex)
				{
					decodeCode = ex.Code;
					decodeDetail = ex.Message;
					break;
				}
				catch (Exception ex)
				{
					decodeCode = Result.InternalError;
					decodeDetail = ex.GetType().FullName + ": " + ex.Message;
					break;
				}

				// LIMIT_EXCEEDED means two different things and docs/WIRE.md
				// says how to tell them apart: a buffer too small for the
				// next batch reports the size it needs through written, and a
				// bound that was actually breached does not. A real consumer
				// grows and retries, so the harness does too rather than
				// recording a refusal the ABI did not make.
				if (r == Result.LimitExceeded && written > (ulong)buf.Length)
				{
					buf = new byte[written];
					grownTo = buf.Length;
					continue;
				}

				if (r != Result.Ok)
				{
					decodeCode = r;
					decodeDetail = "decode_next_batch returned " + Name(r);
					break;
				}

				bytes += written;
				batches++;

				if (done != 0)
				{
					break;
				}

				if (o.CancelAfter >= 0 && (int)batches == o.CancelAfter)
				{
					// Set between two decode_next_batch calls, which is the
					// only place the header says it is read.
					*(uint*)cancelFlag.ToPointer() = 1u;
				}
			}

			long peakDuringDecode = PeakRss();
			long managedAfterDecode = GC.GetTotalMemory(true) / 1024;
			long rssAfterCollect = Rss();

			json.Str("decode_code", Name(decodeCode));
			json.Str("decode_detail", decodeDetail);
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

			// The dump is a second walk over the same document on purpose, so
			// the measured decode never carries a list of strings beside it.
			if (o.DumpPath != null || o.CheckPath != null)
			{
				List<string> dump = new List<string>();
				string dumpError = null;
				try
				{
					int index = 0;
					foreach (Primitive p in source.EnumerateView(o.View))
					{
						dump.Add(CanonicalDump.Line(index, p));
						index++;
					}
				}
				catch (AbiException ex)
				{
					dumpError = Name(ex.Code) + ": " + ex.Message;
				}

				json.Str("dump_error", dumpError);
				json.Num("dumped_records", dump.Count);

				if (o.DumpPath != null && dumpError == null)
				{
					File.WriteAllText(o.DumpPath, string.Join("\n", dump) + "\n");
				}

				if (o.CheckPath != null)
				{
					string[] expected = File.Exists(o.CheckPath)
						? File.ReadAllLines(o.CheckPath)
						: Array.Empty<string>();
					string diff = CanonicalDump.FirstDifference(expected, dump);
					json.Str("check", diff ?? "identical");
				}
			}

			CloseDocument(documentHandle);
			Marshal.FreeHGlobal(cancelFlag);
			GC.KeepAlive(buffer);

			json.Num("live_handles", Handles.LiveCount);
			json.Num("peak_rss_kb", PeakRss());
			Emit(json);
			return 0;
		}

		private static void CloseDocument(IntPtr handle)
		{
			DocumentHandle document = Handles.Remove<DocumentHandle>(handle);
			if (document != null)
			{
				document.Dispose();
			}
		}

		private static void Emit(Json json)
		{
			Console.Out.Write(json.ToString());
			Console.Out.Write("\n");
		}

		private static ResolvedLimits Limits(Options o)
		{
			ResolvedLimits l = new ResolvedLimits();
			if (o.MaxInput != 0ul)
			{
				l.MaxInputBytes = o.MaxInput;
			}

			if (o.MaxEntities != 0ul)
			{
				l.MaxEntities = o.MaxEntities;
			}

			if (o.MaxString != 0ul)
			{
				l.MaxStringBytes = o.MaxString;
			}

			if (o.MaxPolyline != 0ul)
			{
				l.MaxPolylinePoints = o.MaxPolyline;
			}

			if (o.MaxDepth != 0u)
			{
				l.MaxBlockDepth = o.MaxDepth;
			}

			if (o.MaxOutput != 0ul)
			{
				l.MaxOutputBytes = o.MaxOutput;
			}

			return l;
		}

		private static string Name(uint code)
		{
			switch (code)
			{
				case Result.Ok: return "OK";
				case Result.InvalidArgument: return "INVALID_ARGUMENT";
				case Result.UnsupportedFormat: return "UNSUPPORTED_FORMAT";
				case Result.CorruptInput: return "CORRUPT_INPUT";
				case Result.UnsupportedEntity: return "UNSUPPORTED_ENTITY";
				case Result.OutOfMemory: return "OUT_OF_MEMORY";
				case Result.Canceled: return "CANCELED";
				case Result.InternalError: return "INTERNAL_ERROR";
				case Result.AbiMismatch: return "ABI_MISMATCH";
				case Result.LimitExceeded: return "LIMIT_EXCEEDED";
				default: return "UNKNOWN_" + code.ToString(CultureInfo.InvariantCulture);
			}
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
					case "--memory":
						o.Memory = true;
						break;
					case "--no-decode":
						o.NoDecode = true;
						break;
					case "--view":
						o.View = int.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--batch":
						o.BatchBytes = int.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--max-input":
						o.MaxInput = ulong.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--max-entities":
						o.MaxEntities = ulong.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--max-string":
						o.MaxString = ulong.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--max-polyline":
						o.MaxPolyline = ulong.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--max-depth":
						o.MaxDepth = uint.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--max-output":
						o.MaxOutput = ulong.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--cancel-after":
						o.CancelAfter = int.Parse(args[++i], CultureInfo.InvariantCulture);
						break;
					case "--dump":
						o.DumpPath = args[++i];
						break;
					case "--check":
						o.CheckPath = args[++i];
						break;
					default:
						error = "unknown option " + a;
						return o;
				}
			}

			return o;
		}
	}
}
