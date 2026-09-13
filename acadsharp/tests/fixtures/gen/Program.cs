using System;
using System.Diagnostics;
using Viprs;

public static class Program
{
	public static int Main(string[] args)
	{
		if (args.Length < 2)
		{
			Console.Error.WriteLine("usage: fixturegen write|write-codepage|read|corpus|corpus-large|fanout|decode <path> [options]");
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

		// G1.3 adds the corpus and the scenario runner. Same program on
		// purpose: one build, one set of ACadSharp bindings, and the fixture
		// writer sitting next to the thing that reads what it wrote.
		if (args[0] == "corpus")
		{
			foreach (string name in Viprs.Cad.Fixtures.Corpus.WriteAll(args[1], args.Length > 2 ? args[2] : null))
			{
				Console.Out.Write(name);
				Console.Out.Write("\n");
			}

			return 0;
		}

		if (args[0] == "corpus-large")
		{
			int points = args.Length > 2
				? int.Parse(args[2], System.Globalization.CultureInfo.InvariantCulture)
				: 600000;
			Viprs.Cad.Fixtures.Corpus.WriteLarge(args[1], points);
			Console.Error.WriteLine("wrote " + args[1] + " with " + points + " points");
			return 0;
		}

		if (args[0] == "fanout")
		{
			string[] rest = new string[args.Length - 1];
			Array.Copy(args, 1, rest, 0, rest.Length);
			return Viprs.Cad.Fixtures.Harness.Fanout(rest);
		}

		if (args[0] == "decode")
		{
			string[] rest = new string[args.Length - 1];
			Array.Copy(args, 1, rest, 0, rest.Length);
			return Viprs.Cad.Fixtures.Harness.Decode(rest);
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
