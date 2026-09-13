using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

// The C ABI as the managed side sees it: the result codes, the three structs
// laid out exactly as include/viprs_acadsharp.h declares them, the handle
// table that keeps a bad pointer from becoming a fault, and the two helpers
// every export needs.
//
// The structs keep the header's field names verbatim rather than the usual
// C# casing. It reads oddly for about a minute, and then it means the two
// declarations can be compared as written, by a test with no compiler, which
// is worth more than the convention: a field in the wrong place here does not
// fail to build and does not throw, it reads its neighbour's bytes.
namespace Viprs.Abi
{
	// Plain constants, matching the header's #defines by number. Downstream
	// switches on the number, so these are frozen.
	internal static class Result
	{
		public const uint Ok = 0u;
		public const uint InvalidArgument = 1u;
		public const uint UnsupportedFormat = 2u;
		public const uint CorruptInput = 3u;
		public const uint UnsupportedEntity = 4u;
		public const uint OutOfMemory = 5u;
		public const uint Canceled = 6u;
		public const uint InternalError = 7u;
		public const uint AbiMismatch = 8u;
		public const uint LimitExceeded = 9u;
	}

	// A failure with a code the caller should see, as opposed to a bug, which
	// the catch-all turns into InternalError. Every export catches this one
	// first and returns its code.
	internal sealed class AbiException : Exception
	{
		public readonly uint Code;

		public AbiException(uint code, string message)
			: base(message)
		{
			Code = code;
		}
	}

	[StructLayout(LayoutKind.Sequential)]
	public struct viprs_acad_limits_v1
	{
		public uint struct_size;
		public uint struct_version;
		public ulong max_input_bytes;
		public ulong max_entities;
		public ulong max_string_bytes;
		public ulong max_polyline_points;
		public uint max_block_depth;
		public uint reserved0;
		public ulong max_output_bytes;
	}

	[StructLayout(LayoutKind.Sequential)]
	public struct viprs_acad_capabilities_v1
	{
		public uint struct_size;
		public uint struct_version;
		public uint abi_version;
		public uint wire_version;
		public uint dwg_version_min;
		public uint dwg_version_max;
		public byte supports_block_expansion;
		public byte supports_warnings;
		public byte reserved0;
		public byte reserved1;
		public uint reserved2;
	}

	[StructLayout(LayoutKind.Sequential)]
	public struct viprs_view_info_v1
	{
		public uint struct_size;
		public uint struct_version;
		public uint index;
		public uint kind;
		public double min_x;
		public double min_y;
		public double max_x;
		public double max_y;
		public ulong entity_count;
	}

	internal static class AbiConstants
	{
		public const uint AbiVersion = 1u;
		public const uint WireVersion = 2u;
		public const uint StructVersion = 1u;

		// The inclusive AC10xx range the backing reader handles. ADR 0001
		// took these from upstream's own reader table for the pinned version.
		public const uint DwgVersionMin = 1014u;
		public const uint DwgVersionMax = 1032u;

		// 1 now that the ACadSharp adapter lands (libviprs/libviprs-dep#47)
		// and expands a nested INSERT into transformed primitives, bounded by
		// max_block_depth. It is a statement about this build, so it moved
		// with the build rather than ahead of it.
		public const byte SupportsBlockExpansion = 1;
		public const byte SupportsWarnings = 1;

		public const uint ViewKindModel = 0u;
		public const uint ViewKindLayout = 1u;
		public const uint ViewKindUnknown = 2u;
	}

	// The bounds a decode runs under, already resolved: a null limits pointer
	// and a zero field both mean the default, so nothing downstream has to ask
	// whether a number came from the caller.
	//
	// Every field is readonly and there is one way in. Defaults used to be a
	// shared static whose fields anything holding it could assign, so one line
	// in one caller could lower max_entities for every decode in the process,
	// including decodes already running on other threads, and nothing would
	// have reported it. A decode's bounds are settled when it is created.
	internal sealed class ResolvedLimits
	{
		public const ulong DefaultMaxInputBytes = 536870912ul;
		public const ulong DefaultMaxEntities = 20000000ul;
		public const ulong DefaultMaxStringBytes = 65536ul;
		public const ulong DefaultMaxPolylinePoints = 1000000ul;
		public const uint DefaultMaxBlockDepth = 64u;
		public const ulong DefaultMaxOutputBytes = 4294967296ul;

		public readonly ulong MaxInputBytes;
		public readonly ulong MaxEntities;
		public readonly ulong MaxStringBytes;
		public readonly ulong MaxPolylinePoints;
		public readonly uint MaxBlockDepth;
		public readonly ulong MaxOutputBytes;

		// Zero is the default in every field, which is the header's rule, so
		// the resolution happens here rather than in each caller.
		public ResolvedLimits(
			ulong maxInputBytes = 0ul,
			ulong maxEntities = 0ul,
			ulong maxStringBytes = 0ul,
			ulong maxPolylinePoints = 0ul,
			uint maxBlockDepth = 0u,
			ulong maxOutputBytes = 0ul
		)
		{
			MaxInputBytes = Pick(maxInputBytes, DefaultMaxInputBytes);
			MaxEntities = Pick(maxEntities, DefaultMaxEntities);
			MaxStringBytes = Pick(maxStringBytes, DefaultMaxStringBytes);
			MaxPolylinePoints = Pick(maxPolylinePoints, DefaultMaxPolylinePoints);
			MaxBlockDepth = maxBlockDepth == 0u ? DefaultMaxBlockDepth : maxBlockDepth;
			MaxOutputBytes = Pick(maxOutputBytes, DefaultMaxOutputBytes);
		}

		public static readonly ResolvedLimits Defaults = new ResolvedLimits();

		// Returns null when the caller handed over a struct this build does
		// not recognise, which the export turns into InvalidArgument rather
		// than reading past what the caller allocated.
		public static unsafe ResolvedLimits From(viprs_acad_limits_v1* p)
		{
			if (p == null)
			{
				return Defaults;
			}

			if (p->struct_size != (uint)sizeof(viprs_acad_limits_v1))
			{
				return null;
			}

			if (p->struct_version != AbiConstants.StructVersion)
			{
				return null;
			}

			return new ResolvedLimits(
				p->max_input_bytes,
				p->max_entities,
				p->max_string_bytes,
				p->max_polyline_points,
				p->max_block_depth,
				p->max_output_bytes
			);
		}

		private static ulong Pick(ulong given, ulong fallback)
		{
			return given == 0ul ? fallback : given;
		}
	}

	// Handles are counter-issued identifiers cast to a pointer, never an
	// address. A consumer that passes something this library never issued
	// misses the table and gets InvalidArgument, where a real pointer would
	// have been dereferenced.
	internal static class Handles
	{
		private static readonly object Gate = new object();
		private static readonly Dictionary<long, object> Live = new Dictionary<long, object>();
		private static long _next = 1;

		public static IntPtr Add(object value)
		{
			lock (Gate)
			{
				long id = _next;
				_next = _next + 1;
				Live[id] = value;
				return new IntPtr(id);
			}
		}

		public static T Get<T>(IntPtr handle)
			where T : class
		{
			if (handle == IntPtr.Zero)
			{
				return null;
			}

			lock (Gate)
			{
				object found;
				return Live.TryGetValue(handle.ToInt64(), out found) ? found as T : null;
			}
		}

		public static T Remove<T>(IntPtr handle)
			where T : class
		{
			if (handle == IntPtr.Zero)
			{
				return null;
			}

			lock (Gate)
			{
				object found;
				long key = handle.ToInt64();
				if (!Live.TryGetValue(key, out found))
				{
					return null;
				}

				// Cast first, evict second, and never the other way round.
				// Evicting first means a close called with the wrong handle
				// type takes the entry out of the table and then fails the
				// cast, so the object is orphaned: nothing can reach it to
				// release it, and nothing can reach it to report that it is
				// still there. A document closed that way keeps every decode
				// it tracked, and each of those keeps the whole parsed
				// drawing. The caller has no way back, and no code told it.
				T typed = found as T;
				if (typed == null)
				{
					return null;
				}

				Live.Remove(key);
				return typed;
			}
		}

		// How many handles this library has issued and not yet released. Only
		// the test exports read it; it exists because "closing the wrong
		// handle leaks the right one" is not observable from the outside
		// otherwise, and a leak nobody can see is a leak nobody fixes.
		public static ulong LiveCount
		{
			get
			{
				lock (Gate)
				{
					return (ulong)Live.Count;
				}
			}
		}
	}

	// The one string convention on this boundary, in one place.
	internal static class Utf8Out
	{
		// cap 0 with a null buffer is the documented sizing call and is not a
		// failure. A non-null buffer that is too small writes nothing at all,
		// because a truncated UTF-8 string can end mid-sequence and a consumer
		// cannot tell that from a complete one.
		public static unsafe uint Write(string value, byte* buffer, ulong cap, ulong* required)
		{
			if (required == null)
			{
				return Result.InvalidArgument;
			}

			byte[] bytes = Encoding.UTF8.GetBytes(value ?? string.Empty);
			*required = (ulong)bytes.Length;

			if (buffer == null)
			{
				return cap == 0ul ? Result.Ok : Result.InvalidArgument;
			}

			if (cap < (ulong)bytes.Length)
			{
				return Result.LimitExceeded;
			}

			for (int i = 0; i < bytes.Length; i++)
			{
				buffer[i] = bytes[i];
			}

			return Result.Ok;
		}
	}
}
