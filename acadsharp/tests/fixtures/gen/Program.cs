using System;
using System.Diagnostics;
using Viprs;

public static class Program
{
	public static int Main(string[] args)
	{
		if (args.Length < 2)
		{
			Console.Error.WriteLine("usage: fixturegen write|write-codepage|read <path>");
			return 2;
		}

		if (args[0] == "write")
		{
			Probe.WriteFixture(args[1]);
			Console.Error.WriteLine("wrote " + args[1]);
			return 0;
		}

		if (args[0] == "write-codepage")
		{
			Probe.WriteCodePageFixture(args[1]);
			Console.Error.WriteLine("wrote " + args[1]);
			return 0;
		}

		if (args[0] == "read")
		{
			Stopwatch sw = Stopwatch.StartNew();
			string json = Probe.Describe(args[1]);
			sw.Stop();
			Console.Out.Write(json);
			Console.Out.Write("\n");
			Console.Error.WriteLine("read_ms=" + sw.ElapsedMilliseconds);
			return 0;
		}

		Console.Error.WriteLine("unknown mode " + args[0]);
		return 2;
	}
}
