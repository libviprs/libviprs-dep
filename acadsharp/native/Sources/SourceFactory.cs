using System;
using System.IO;
using Viprs.Abi;

// The one place that decides which source opens an input.
//
// Deliberately tiny. The adapter issue adds one branch to each method, and a
// one-line change is one line of merge conflict rather than a rewrite of a
// file two people touched.
namespace Viprs.Sources
{
	internal static class SourceFactory
	{
		public static IDocumentSource OpenMemory(byte[] data, ResolvedLimits limits)
		{
			if (SyntheticSource.Matches(data))
			{
				return SyntheticSource.Open(data);
			}

			if (AcadSharpSource.Matches(data))
			{
				return AcadSharpSource.OpenMemory(data, limits);
			}

			throw new AbiException(
				Result.UnsupportedFormat,
				"no source in this build recognises these bytes"
			);
		}

		public static IDocumentSource OpenPath(string path, ResolvedLimits limits)
		{
			// max_input_bytes first, before anything opens the file.
			//
			// The bound is about the size of the input, and the size of the
			// input is a stat: deciding it needs no idea which source would
			// have handled the bytes. Reading the head first would make the
			// refusal cost an open, which is exactly what a host setting this
			// bound is trying to avoid, and it would turn an unreadable file
			// into an IO failure where the answer is "too big".
			FileLength(path, limits ?? ResolvedLimits.Defaults);

			byte[] head = ReadHead(path, SyntheticSource.MagicLength);
			if (SyntheticSource.Matches(head))
			{
				return SyntheticSource.Open(File.ReadAllBytes(path));
			}

			// By path rather than from a copy of the file, which is the whole
			// difference between this call and OpenMemory.
			if (AcadSharpSource.Matches(ReadHead(path, AcadSharpSource.MagicLength)))
			{
				return AcadSharpSource.OpenPath(path, limits);
			}

			throw new AbiException(
				Result.UnsupportedFormat,
				"no source in this build recognises this file"
			);
		}

		// The file's length, refusing before it is opened when the caller's
		// max_input_bytes says so. A missing path is the caller's argument
		// being wrong; a path that exists and cannot be stat'ed is the input's
		// problem. Neither is an internal error, and both used to be one.
		private static long FileLength(string path, ResolvedLimits limits)
		{
			long length;
			try
			{
				FileInfo info = new FileInfo(path);
				if (!info.Exists)
				{
					throw new AbiException(Result.InvalidArgument, "no file at the given path");
				}

				length = info.Length;
			}
			catch (AbiException)
			{
				throw;
			}
			catch (Exception ex)
			{
				throw new AbiException(
					Result.CorruptInput,
					ex.GetType().Name + ": " + ex.Message
				);
			}

			if ((ulong)length > limits.MaxInputBytes)
			{
				throw new AbiException(
					Result.LimitExceeded,
					"the file is " + length + " bytes and max_input_bytes is "
						+ limits.MaxInputBytes
				);
			}

			return length;
		}

		private static byte[] ReadHead(string path, int count)
		{
			try
			{
				return ReadHeadCore(path, count);
			}
			catch (Exception ex)
			{
				// An input this process cannot read is the input's problem,
				// not a bug in this library, so it gets a code a caller can
				// act on rather than INTERNAL_ERROR.
				throw new AbiException(
					Result.CorruptInput,
					ex.GetType().Name + ": " + ex.Message
				);
			}
		}

		private static byte[] ReadHeadCore(string path, int count)
		{
			using (FileStream stream = File.OpenRead(path))
			{
				byte[] head = new byte[count];
				int read = 0;
				while (read < count)
				{
					int n = stream.Read(head, read, count - read);
					if (n <= 0)
					{
						break;
					}

					read += n;
				}

				if (read < count)
				{
					return Array.Empty<byte>();
				}

				return head;
			}
		}
	}
}
