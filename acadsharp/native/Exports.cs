using System;
using System.Runtime.InteropServices;
using System.Text;
using Viprs;

// Exports are static, take and return primitives and pointers only, and never
// let a managed exception escape: every failure comes back as a negative code.
public static class Exports
{
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_abi_version")]
	public static uint AbiVersion() => 1u;

	// Returns the number of bytes written into out_buf, or a negative code.
	//   -1 buffer too small (out_len holds the required size on entry failure)
	//   -2 managed exception while reading
	//   -3 bad arguments
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_describe")]
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
	[UnmanagedCallersOnly(EntryPoint = "viprs_acad_entity_count")]
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
}
