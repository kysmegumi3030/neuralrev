// =============================================================================
// offline_renderer.cpp  —  TEMPLATE for plugin_match.OfflineRenderer
// -----------------------------------------------------------------------------
// A tiny host that runs YOUR shipped DSP outside the plugin/JUCE, so Python can
// A/B it against the competitor. It compiles your real DSP header (single source
// of truth) and speaks the raw-float32 protocol OfflineRenderer expects:
//
//   argv:   <exe> <sampleRate> <blockSize> <numChannels> <p0> <p1> ... <pN>
//   stdin:  interleaved float32 frames  (numChannels values per frame)
//   stdout: interleaved float32 frames  (numChannels values per frame)
//
// Build (clang/gcc):
//   clang++ -std=c++17 -O2 -I<your-include-dirs> offline_renderer.cpp -o my_dsp.exe
//
// TO ADAPT (3 edits marked >>>):
//   1) #include your DSP header.
//   2) set N_PARAMS to your parameter count.
//   3) call your DSP's prepare() and setParameters()/process() in the marked spots.
//
// The _setmode calls are ESSENTIAL on Windows — without binary mode, stdin/stdout
// translate CR/LF bytes and corrupt the float stream.
// =============================================================================
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <algorithm>
#if defined(_WIN32)
#include <io.h>
#include <fcntl.h>
#endif

// >>> 1) include your shipped DSP (adjust the relative path):
// #include "../../src/dsp/YourDsp.hpp"

// >>> 2) number of normalized parameters your setParameters() takes:
static constexpr int N_PARAMS = 16;

// Minimal stand-in for juce::dsp::AudioBlock / ProcessContextReplacing so a
// JUCE-style process(context) works unchanged. If your DSP has a different
// process signature, call it directly in the loop instead.
struct Block {
    std::vector<float*> ch; int n;
    size_t getNumChannels() const { return ch.size(); }
    size_t getNumSamples()  const { return (size_t)n; }
    float* getChannelPointer(size_t c) const { return ch[c]; }
};
struct Ctx { Block blk; const Block& getOutputBlock() const { return blk; } };

int main(int argc, char** argv) {
#if defined(_WIN32)
    _setmode(_fileno(stdin),  _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    if (argc < 4) { fprintf(stderr, "usage: sr block nch p0..pN\n"); return 2; }
    double sr = atof(argv[1]);
    int block = atoi(argv[2]);
    int nch   = atoi(argv[3]);
    float p[N_PARAMS];
    for (int i = 0; i < N_PARAMS; ++i)
        p[i] = (argc > 4 + i) ? (float)atof(argv[4 + i]) : 0.0f;

    // read all input frames from stdin (interleaved f32) -> per-channel buffers
    std::vector<float> in;
    { float buf[4096]; size_t r;
      while ((r = fread(buf, sizeof(float), 4096, stdin)) > 0) in.insert(in.end(), buf, buf + r); }
    size_t frames = nch ? in.size() / (size_t)nch : 0;
    std::vector<std::vector<float>> chans((size_t)nch, std::vector<float>(frames));
    for (size_t i = 0; i < frames; ++i)
        for (int c = 0; c < nch; ++c)
            chans[(size_t)c][i] = in[i * (size_t)nch + (size_t)c];

    // >>> 3a) construct + prepare your DSP:
    // YourDsp dsp; dsp.prepare(sr, nch);
    (void)sr;

    // >>> 3b) helper to push the current params into your DSP each block
    //         (mirror however PluginProcessor calls setParameters):
    // auto setp = [&]{ dsp.setParameters(p[0], p[1], /* ... */ p[N_PARAMS-1]); };
    // setp();

    for (size_t pos = 0; pos < frames; pos += (size_t)block) {
        int n = (int)std::min((size_t)block, frames - pos);
        Ctx ctx; ctx.blk.n = n;
        for (int c = 0; c < nch; ++c) ctx.blk.ch.push_back(&chans[(size_t)c][pos]);
        // >>> 3c) run your DSP for this block (in-place on the channel pointers):
        // setp();
        // dsp.process(ctx);
    }

    std::vector<float> out(frames * (size_t)nch);
    for (size_t i = 0; i < frames; ++i)
        for (int c = 0; c < nch; ++c)
            out[i * (size_t)nch + (size_t)c] = chans[(size_t)c][i];
    fwrite(out.data(), sizeof(float), out.size(), stdout);
    return 0;
}
