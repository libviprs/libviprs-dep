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

		private static byte[] ReadHead(string path, int count)
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
