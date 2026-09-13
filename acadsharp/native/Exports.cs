using System;
using System.Runtime.InteropServices;
using System.Text;
using Viprs;
using Viprs.Abi;
using Viprs.Sources;
using Viprs.Wire;

// The unmanaged surface of include/viprs_acadsharp.h.
//
// Every entry point here has the same three jobs, and none of them is visible
// in a signature. Validate every pointer and every length before touching one,
// because a null dereference in here is a segfault in the consumer's process
// and there is no runtime in between to say so. Turn every failure into a
// numeric result code. Catch everything on the way out, because a managed
// exception that reaches unmanaged code is not an exception any more, it is an
// abort with no stack the caller can read.
//
// A wrapper that does two of the three looks exactly like one that does all
// three, right up until a consumer passes a null. So the shape is repeated
// rather than abstracted: one try, one AbiException catch that carries a code
// the caller should see, one catch-all that turns a bug into INTERNAL_ERROR.
public static class Exports
{
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_abi_version")]
	public static uint AbiVersion() => AbiConstants.AbiVersion;

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_abi_fingerprint")]
	public static ulong AbiFingerprintOf() => AbiFingerprint.Value;

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_capabilities_v1")]
	public static unsafe uint Capabilities(
		viprs_acad_capabilities_v1* outCaps,
		byte* versionUtf8,
		ulong cap,
		ulong* required
	)
	{
		try
		{
			if (outCaps == null || required == null)
			{
				return Result.InvalidArgument;
			}

			if (outCaps->struct_size != (uint)sizeof(viprs_acad_capabilities_v1))
			{
				return Result.InvalidArgument;
			}

			if (outCaps->struct_version != AbiConstants.StructVersion)
			{
				return Result.InvalidArgument;
			}

			uint wrote = Utf8Out.Write(AcadSharpVersion.Text, versionUtf8, cap, required);
			if (wrote != Result.Ok)
			{
				return wrote;
			}

			outCaps->abi_version = AbiConstants.AbiVersion;
			outCaps->wire_version = AbiConstants.WireVersion;
			outCaps->dwg_version_min = AbiConstants.DwgVersionMin;
			outCaps->dwg_version_max = AbiConstants.DwgVersionMax;
			outCaps->supports_block_expansion = AbiConstants.SupportsBlockExpansion;
			outCaps->supports_warnings = AbiConstants.SupportsWarnings;
			outCaps->reserved0 = 0;
			outCaps->reserved1 = 0;
			outCaps->reserved2 = 0u;
			return Result.Ok;
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_open_path_utf8")]
	public static unsafe uint OpenPathUtf8(
		byte* path,
		ulong pathLen,
		viprs_acad_limits_v1* limits,
		IntPtr* outHandle
	)
	{
		try
		{
			if (outHandle == null)
			{
				return Result.InvalidArgument;
			}

			*outHandle = IntPtr.Zero;

			if (path == null || pathLen == 0ul || pathLen > (ulong)int.MaxValue)
			{
				return Result.InvalidArgument;
			}

			ResolvedLimits resolved = ResolvedLimits.From(limits);
			if (resolved == null)
			{
				return Result.InvalidArgument;
			}

			string text = Encoding.UTF8.GetString(path, (int)pathLen);
			if (text.Length == 0 || text.IndexOf('\0') >= 0)
			{
				// An embedded NUL is the caller's argument being wrong, not
				// the input's problem, and it is the one malformed path the
				// platform rejects before it ever stats anything. Refusing it
				// here keeps it INVALID_ARGUMENT, which is what ABI.md says a
				// path that names nothing readable is. Everything else about
				// the path is SourceFactory's.
				return Result.InvalidArgument;
			}

			// No stat and no max_input_bytes here. Both used to be, and this
			// copy mapped a failed stat to INVALID_ARGUMENT where
			// SourceFactory maps it to CORRUPT_INPUT, so the code a caller
			// saw depended on which of the two noticed first. SourceFactory
			// stats once, refuses a missing path with INVALID_ARGUMENT and
			// applies the bound before the file is opened.
			IDocumentSource source = SourceFactory.OpenPath(text, resolved);
			*outHandle = Handles.Add(new DocumentHandle(source, resolved));
			return Result.Ok;
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_open_memory")]
	public static unsafe uint OpenMemory(
		byte* data,
		ulong dataLen,
		viprs_acad_limits_v1* limits,
		IntPtr* outHandle
	)
	{
		try
		{
			if (outHandle == null)
			{
				return Result.InvalidArgument;
			}

			*outHandle = IntPtr.Zero;

			if (data == null || dataLen == 0ul)
			{
				return Result.InvalidArgument;
			}

			ResolvedLimits resolved = ResolvedLimits.From(limits);
			if (resolved == null)
			{
				return Result.InvalidArgument;
			}

			if (dataLen > (ulong)int.MaxValue)
			{
				return Result.LimitExceeded;
			}

			// max_input_bytes, before the copy below rather than after it.
			// SourceFactory owns the number and the message; this asks it,
			// because a bound applied after the duplication has already paid
			// for the allocation it exists to refuse.
			SourceFactory.CheckInputBytes(dataLen, resolved, "buffer");

			// The header says this call duplicates the input and open_path
			// does not, and this is where that is true: the caller owns the
			// buffer for the duration of the call only, so anything kept has
			// to be a copy.
			byte[] copy = new byte[(int)dataLen];
			Marshal.Copy((IntPtr)data, copy, 0, copy.Length);

			IDocumentSource source = SourceFactory.OpenMemory(copy, resolved);
			*outHandle = Handles.Add(new DocumentHandle(source, resolved));
			return Result.Ok;
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_view_count")]
	public static unsafe uint ViewCount(IntPtr handle, uint* outCount)
	{
		try
		{
			if (outCount == null)
			{
				return Result.InvalidArgument;
			}

			*outCount = 0u;

			DocumentHandle document = Handles.Get<DocumentHandle>(handle);
			if (document == null || document.Closed)
			{
				return Result.InvalidArgument;
			}

			*outCount = (uint)document.Source.ViewCount;
			return Result.Ok;
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_view_info_v1")]
	public static unsafe uint ViewInfo(
		IntPtr handle,
		uint index,
		viprs_view_info_v1* outInfo,
		byte* nameUtf8,
		ulong nameCap,
		ulong* nameRequired
	)
	{
		try
		{
			if (outInfo == null || nameRequired == null)
			{
				return Result.InvalidArgument;
			}

			if (outInfo->struct_size != (uint)sizeof(viprs_view_info_v1))
			{
				return Result.InvalidArgument;
			}

			if (outInfo->struct_version != AbiConstants.StructVersion)
			{
				return Result.InvalidArgument;
			}

			DocumentHandle document = Handles.Get<DocumentHandle>(handle);
			if (document == null || document.Closed)
			{
				return Result.InvalidArgument;
			}

			SourceView view;
			if (index > (uint)int.MaxValue || !document.Source.TryGetView((int)index, out view))
			{
				return Result.InvalidArgument;
			}

			uint wrote = Utf8Out.Write(view.Name, nameUtf8, nameCap, nameRequired);
			if (wrote != Result.Ok)
			{
				return wrote;
			}

			outInfo->index = index;
			outInfo->kind = view.Kind;
			outInfo->min_x = view.MinX;
			outInfo->min_y = view.MinY;
			outInfo->max_x = view.MaxX;
			outInfo->max_y = view.MaxY;
			outInfo->entity_count = view.ItemCount;
			return Result.Ok;
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_decode_begin")]
	public static unsafe uint DecodeBegin(
		IntPtr handle,
		uint viewIndex,
		uint* cancelFlag,
		IntPtr* outHandle
	)
	{
		try
		{
			if (outHandle == null)
			{
				return Result.InvalidArgument;
			}

			*outHandle = IntPtr.Zero;

			DocumentHandle document = Handles.Get<DocumentHandle>(handle);
			if (document == null || document.Closed)
			{
				return Result.InvalidArgument;
			}

			SourceView view;
			if (
				viewIndex > (uint)int.MaxValue
				|| !document.Source.TryGetView((int)viewIndex, out view)
			)
			{
				return Result.InvalidArgument;
			}

			DecodeSession session = new DecodeSession(document, viewIndex, (IntPtr)cancelFlag);
			IntPtr decode = Handles.Add(session);
			document.Track(decode);
			*outHandle = decode;
			return Result.Ok;
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_decode_next_batch")]
	public static unsafe uint DecodeNextBatch(
		IntPtr handle,
		byte* buf,
		ulong cap,
		ulong* written,
		byte* done
	)
	{
		try
		{
			if (written == null || done == null)
			{
				return Result.InvalidArgument;
			}

			*written = 0ul;
			*done = 0;

			if (buf == null || cap == 0ul)
			{
				return Result.InvalidArgument;
			}

			DecodeSession session = Handles.Get<DecodeSession>(handle);
			if (session == null)
			{
				return Result.InvalidArgument;
			}

			ulong wrote;
			byte finished;
			uint code = session.NextBatch(buf, cap, out wrote, out finished);
			*written = wrote;
			*done = finished;
			return code;
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_decode_close")]
	public static void DecodeClose(IntPtr handle)
	{
		try
		{
			DecodeSession session = Handles.Remove<DecodeSession>(handle);
			if (session != null)
			{
				// Out of the document's list as well as out of the handle
				// table. Releasing only the table leaves the document holding
				// a handle that no longer resolves, once per decode the
				// caller opened, for as long as the document is open.
				session.Document.Untrack(handle);
				session.Dispose();
			}
		}
		catch (Exception)
		{
			// Documented as never failing, so it cannot report and must not
			// throw. A close that throws leaves the caller holding a handle
			// with no way to release it and no code to look at.
		}
	}

	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_close")]
	public static void Close(IntPtr handle)
	{
		try
		{
			DocumentHandle document = Handles.Remove<DocumentHandle>(handle);
			if (document != null)
			{
				document.Dispose();
			}
		}
		catch (Exception)
		{
			// As above.
		}
	}

#if VIPRS_ACAD_TEST_EXPORTS
	// Compiled only in the Test configuration.
	//
	// It is here because "no managed exception escapes" is otherwise a claim
	// backed by nothing: every other export is supposed not to throw, so none
	// of them can prove the catch-all works. This one throws on purpose, in
	// four different ways, and the C conformance consumer checks that each
	// comes back as INTERNAL_ERROR and that the process is still running
	// afterwards.
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad__test_throw")]
	public static uint TestThrow(uint kind)
	{
		try
		{
			if (kind == 1u)
			{
				throw new InvalidOperationException("thrown on purpose by the test export");
			}

			if (kind == 2u)
			{
				string nothing = null;
				return (uint)nothing.Length;
			}

			if (kind == 3u)
			{
				int[] one = new int[1];
				return (uint)one[7];
			}

			if (kind == 4u)
			{
				throw new AbiException(Result.CorruptInput, "a coded failure, not a bug");
			}

			throw new Exception("thrown on purpose by the test export");
		}
		catch (AbiException ex)
		{
			return ex.Code;
		}
		catch (Exception)
		{
			return Result.InternalError;
		}
	}

	// How many handles this library has issued and not released.
	//
	// It exists because the interesting handle bugs are invisible from
	// outside: closing a document with the wrong close function used to
	// orphan it and every decode it tracked, and from the caller's side that
	// looked exactly like a close that worked. A count a consumer can read
	// before and after makes it a test rather than an argument. The malformed
	// corpus reads it after every refusal.
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad__test_live_handles")]
	public static ulong TestLiveHandles()
	{
		try
		{
			return Handles.LiveCount;
		}
		catch (Exception)
		{
			return ulong.MaxValue;
		}
	}

	// ---------------------------------------------------------------------
	// G1.1's spike exports, now inside the test configuration.
	//
	// They are not on the frozen ABI and nothing is generated from them, and
	// until this change they shipped in every archive. That was the problem:
	// `viprs_acad__spike_entity_count` calls `DwgReader.Read(path)` with no
	// version gate, no limits struct and no cancel flag, so the library
	// offered a second door onto untrusted DWG with none of the locks the
	// first one has. They also break four rules the header states, reporting
	// through negative `int` codes, taking null-terminated paths, and writing
	// an exception message into the caller's buffer, which ABI.md forbids
	// outright.
	//
	// They are not deleted, because `test_acadsharp_recorded_parity.py` says
	// in its own docstring that a later change which recompiles the shim "has
	// to face this diff rather than describe it", and re-recording the AOT
	// side of those captures is what `describe` exists for. Deleting them
	// would strand that evidence with no way to reproduce it.
	//
	// So they keep their behaviour, gain the double underscore that marks
	// every other test-only export, and move behind the same configuration.
	// A release build no longer has them at all, which is the half that
	// mattered.
	// ---------------------------------------------------------------------

	// Returns the number of bytes written into out_buf, or a negative code.
	//   -1 buffer too small (out_len holds the required size on entry failure)
	//   -2 managed exception while reading
	//   -3 bad arguments
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad__spike_describe")]
	public static int Describe(IntPtr pathUtf8, IntPtr outBuf, int outLen)
	{
		try
		{
			if (pathUtf8 == IntPtr.Zero || outBuf == IntPtr.Zero || outLen <= 0)
			{
				return -3;
			}

			string path = Marshal.PtrToStringUTF8(pathUtf8);
			string json = Probe.Describe(path);
			byte[] bytes = Encoding.UTF8.GetBytes(json);
			if (bytes.Length > outLen)
			{
				return -1;
			}

			Marshal.Copy(bytes, 0, outBuf, bytes.Length);
			return bytes.Length;
		}
		catch (Exception ex)
		{
			WriteError(outBuf, outLen, ex.GetType().FullName + ": " + ex.Message);
			return -2;
		}
	}

	private static void WriteError(IntPtr outBuf, int outLen, string message)
	{
		try
		{
			byte[] bytes = Encoding.UTF8.GetBytes(message);
			int n = bytes.Length < outLen ? bytes.Length : outLen;
			Marshal.Copy(bytes, 0, outBuf, n);
		}
		catch (Exception)
		{
		}
	}

	// The entity count on its own, so the smoke program can check a number
	// without parsing anything.
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad__spike_entity_count")]
	public static int EntityCount(IntPtr pathUtf8)
	{
		try
		{
			if (pathUtf8 == IntPtr.Zero)
			{
				return -3;
			}

			string path = Marshal.PtrToStringUTF8(pathUtf8);
			return ACadSharp.IO.DwgReader.Read(path).Entities.Count;
		}
		catch (Exception)
		{
			return -2;
		}
	}
#endif
}
