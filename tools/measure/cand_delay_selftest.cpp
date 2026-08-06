/**
 * @file  cand_delay_selftest.cpp
 * @brief 候选侧延迟段的自检：编译 + LFO 深度律 + 环内损耗 + 反馈量化
 *
 * 为什么要这个而不是直接进插件：机制类的错误（比如 LFO 调制加错了指针）
 * 在整插件里只表现为「对拍差一点」，很难定位；而这里可以**直接把候选侧
 * 的实测量与参考侧已经定死的实测量并排打印**。
 *
 * 三项自检各自对标一条已定死的参考实测：
 *   1. **LFO 深度律** depth(D) = 2A·|sin(π·D/T)|，A = 3.2755，T = SR/1.70186。
 *      候选侧不实现这条闭式解（只调制写指针），所以这一项检验的是
 *      **机制是否真的推出了那条律** —— 包括 norm=0.65 处的零点。
 *      测法与参考侧同一套：1 kHz 载波 + 解析相位解调。
 *   2. **每圈损耗 L(f)**：窄带猝发读逐圈比值，除以反馈系数。
 *      对标参考的 100 Hz…8 kHz 表（0.046 dB 拟合落点）。
 *   3. **反馈量化**：0.295 / 0.305 应给出**完全相同**的系数（同属第 15 格）。
 *
 * 编译（不需要 JUCE —— DelayEffect.h 用 NREV_NO_JUCE 走替身路径）：
 *   c++ -std=c++17 -O2 -DNREV_NO_JUCE -I src/dsp \
 *       tools/measure/cand_delay_selftest.cpp -o /tmp/cand_delay_selftest
 */

#include <cmath>
#include <complex>
#include <cstdio>
#include <vector>

// ------------------------------------------------------------
// juce::dsp 的极小替身（与 tools/nrev_render 同一套做法：
// 让「被测的算法」与「发布的算法」是同一份源码）
// ------------------------------------------------------------
namespace juce::dsp
{
struct ProcessSpec
{
    double sampleRate;
    unsigned int maximumBlockSize;
    unsigned int numChannels;
};

template <typename T>
class AudioBlock
{
public:
    AudioBlock(T** ch, size_t nch, size_t n) : ch_(ch), nch_(nch), n_(n) {}
    size_t getNumChannels() const { return nch_; }
    size_t getNumSamples() const { return n_; }
    T* getChannelPointer(size_t i) const { return ch_[i]; }
private:
    T** ch_;
    size_t nch_, n_;
};

template <typename T>
struct ProcessContextReplacing
{
    explicit ProcessContextReplacing(AudioBlock<T>& b) : b_(b) {}
    AudioBlock<T>& getOutputBlock() const { return b_; }
    AudioBlock<T>& b_;
};
} // namespace juce::dsp

#include "DelayEffect.h"

using namespace nrev;
namespace DT = nrev::delaytuning;

static constexpr double SR = 48000.0;
static constexpr int AT = 2 * 48000;      // 激励起点，与参考侧一致
static constexpr double AMP = 1e-3;       // 线性区

// ------------------------------------------------------------ 渲染辅助
static void render(DelayEffect& fx, std::vector<float>& l, std::vector<float>& r)
{
    const size_t n = l.size();
    const size_t block = 512;
    for (size_t pos = 0; pos < n; pos += block)
    {
        const size_t len = std::min(block, n - pos);
        float* ch[2] = { l.data() + pos, r.data() + pos };
        juce::dsp::AudioBlock<float> b(ch, 2, len);
        juce::dsp::ProcessContextReplacing<float> ctx(b);
        fx.process(ctx);
    }
}

/// FFT（基 2，就地）—— 只用于解析信号，规模固定为 2 的幂
static void fft(std::vector<std::complex<double>>& a, bool inv)
{
    const size_t n = a.size();
    for (size_t i = 1, j = 0; i < n; ++i)
    {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    for (size_t len = 2; len <= n; len <<= 1)
    {
        const double ang = 2.0 * M_PI / static_cast<double>(len) * (inv ? 1 : -1);
        const std::complex<double> wl(std::cos(ang), std::sin(ang));
        for (size_t i = 0; i < n; i += len)
        {
            std::complex<double> w(1.0, 0.0);
            for (size_t k = 0; k < len / 2; ++k)
            {
                const auto u = a[i + k], v = a[i + k + len / 2] * w;
                a[i + k] = u + v;
                a[i + k + len / 2] = u - v;
                w *= wl;
            }
        }
    }
    if (inv)
        for (auto& x : a) x /= static_cast<double>(n);
}

/// 解析相位（FFT 版 Hilbert）+ 展开
static std::vector<double> analytic_phase(const float* x, size_t n)
{
    size_t m = 1;
    while (m < n) m <<= 1;
    std::vector<std::complex<double>> a(m, {0.0, 0.0});
    for (size_t i = 0; i < n; ++i) a[i] = { static_cast<double>(x[i]), 0.0 };
    fft(a, false);
    for (size_t i = 1; i < m / 2; ++i) a[i] *= 2.0;
    for (size_t i = m / 2 + 1; i < m; ++i) a[i] = 0.0;
    fft(a, true);
    std::vector<double> ph(n);
    double prev = 0.0, off = 0.0;
    for (size_t i = 0; i < n; ++i)
    {
        double p = std::arg(a[i]);
        if (i > 0)
        {
            double d = p - prev;
            while (d > M_PI)  { off -= 2.0 * M_PI; d -= 2.0 * M_PI; }
            while (d < -M_PI) { off += 2.0 * M_PI; d += 2.0 * M_PI; }
        }
        prev = p;
        ph[i] = p + off;
    }
    return ph;
}

// ------------------------------------------------ 1. LFO 深度律
static void test_lfo_depth()
{
    printf("\n%s\n1. LFO 深度律：机制是否推出 depth(D) = 2A|sin(pi D/T)|\n%s\n",
           std::string(84, '=').c_str(), std::string(84, '=').c_str());
    const double T = SR / DT::kMeasLfoRateHz;
    printf("  T = %.2f samples   A = %.5f samples\n", T, DT::kMeasLfoAmpSamples);
    printf("  %6s %8s %8s %10s %10s %10s\n",
           "norm", "ms", "D", "候选实测", "参考律", "差");

    const double norms[] = { 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.63,
                             0.65, 0.7, 0.8, 0.9, 1.0 };
    const double FCAR = 1000.0;
    double worst = 0.0;

    for (double nv : norms)
    {
        const size_t n = static_cast<size_t>(12 * SR);
        std::vector<float> l(n, 0.0f), r(n, 0.0f);
        // 连续 1 kHz 载波（幅度在线性区）
        for (size_t i = 0; i < n; ++i)
        {
            const float s = static_cast<float>(
                AMP * std::sin(2.0 * M_PI * FCAR * static_cast<double>(i) / SR));
            l[i] = s; r[i] = s;
        }

        DelayEffect fx;
        juce::dsp::ProcessSpec spec { SR, 512, 2 };
        fx.prepare(spec);
        // drywet=1 ⇒ 纯湿；feedback=0 ⇒ 只有一次通过，测的是延迟线本身
        fx.setParametersNormalized(1.0f, static_cast<float>(nv),
                                   static_cast<float>(nv), 0.0f, 1.0f, 0.0f, 1.0f);
        render(fx, l, r);

        // 取稳定段解调
        const size_t a0 = static_cast<size_t>(3 * SR), a1 = static_cast<size_t>(11 * SR);
        auto ph = analytic_phase(l.data() + a0, a1 - a0);
        // 去掉载波线性相位，剩下的是延迟调制
        const size_t m = ph.size();
        double sx = 0, sy = 0, sxx = 0, sxy = 0;
        for (size_t i = 0; i < m; ++i)
        {
            const double x = static_cast<double>(i);
            sx += x; sy += ph[i]; sxx += x * x; sxy += x * ph[i];
        }
        const double dn = static_cast<double>(m);
        const double slope = (dn * sxy - sx * sy) / (dn * sxx - sx * sx);
        const double icpt = (sy - slope * sx) / dn;

        // 残差 → 样点偏移；对 LFO 频率做正弦最小二乘取幅度
        const double wl = 2.0 * M_PI * DT::kMeasLfoRateHz / SR;
        double cs = 0, sn2 = 0, cc = 0, ss = 0;
        for (size_t i = 0; i < m; ++i)
        {
            const double x = static_cast<double>(i);
            const double dev = ph[i] - (slope * x + icpt);
            const double dd = -dev * SR / (2.0 * M_PI * FCAR);   // 相位→样点
            cs += dd * std::cos(wl * x);
            sn2 += dd * std::sin(wl * x);
            cc += std::cos(wl * x) * std::cos(wl * x);
            ss += std::sin(wl * x) * std::sin(wl * x);
        }
        const double amp = std::hypot(cs / cc, sn2 / ss);

        const double D = DT::timeMsFromNorm(nv) * 1.0e-3 * SR;
        const double law = DT::lfoNetDepthSamples(D, SR);
        const double diff = amp - law;
        if (std::fabs(diff) > worst) worst = std::fabs(diff);
        printf("  %6.2f %8.1f %8.0f %10.4f %10.4f %+10.4f\n",
               nv, DT::timeMsFromNorm(nv), D, amp, law, diff);
    }
    printf("\n  最差绝对偏差 = %.4f samples   %s\n", worst,
           worst < 0.15 ? "OK 机制推出了那条律" : "FAIL 机制与律不符");
    printf("  参考侧同一条律的最差偏差是 0.0226 samples（18 个延迟档）。\n");
}

// ------------------------------------------------ 2. 每圈损耗
static double band_amp(const float* x, size_t n, double f)
{
    double ss = 0, sc = 0, s2 = 0, c2 = 0;
    for (size_t i = 0; i < n; ++i)
    {
        const double t = 2.0 * M_PI * f * static_cast<double>(i) / SR;
        ss += x[i] * std::sin(t); sc += x[i] * std::cos(t);
        s2 += std::sin(t) * std::sin(t); c2 += std::cos(t) * std::cos(t);
    }
    return std::hypot(ss / s2, sc / c2);
}

static double loop_gain(double f, double fbNorm, double lp, double hp)
{
    const int BURST = 2048;
    const double NT = 0.4;
    const size_t n = static_cast<size_t>(10 * SR);
    std::vector<float> l(n, 0.0f), r(n, 0.0f);
    for (int i = 0; i < BURST; ++i)
    {
        const double w = 0.5 - 0.5 * std::cos(2.0 * M_PI * i / (BURST - 1));
        const float s = static_cast<float>(
            AMP * w * std::sin(2.0 * M_PI * f * i / SR));
        l[AT + i] = s; r[AT + i] = s;
    }
    DelayEffect fx;
    juce::dsp::ProcessSpec spec { SR, 512, 2 };
    fx.prepare(spec);
    fx.setParametersNormalized(1.0f, static_cast<float>(NT), static_cast<float>(NT),
                               static_cast<float>(fbNorm),
                               static_cast<float>(lp), static_cast<float>(hp), 1.0f);
    render(fx, l, r);

    const int D = static_cast<int>(std::lround(DT::timeMsFromNorm(NT) * SR / 1000.0));
    std::vector<double> amps;
    for (int k = 0; k < 7; ++k)
    {
        const int c = AT + k * D;
        const int a = c - 300, b = c + BURST + 300;
        if (b > static_cast<int>(n)) break;
        amps.push_back(band_amp(l.data() + a, static_cast<size_t>(b - a), f));
    }
    double sum = 0; int cnt = 0;
    for (size_t k = 2; k < amps.size(); ++k)
        if (amps[k - 1] > 1e-20) { sum += amps[k] / amps[k - 1]; ++cnt; }
    return cnt ? sum / cnt : NAN;
}

static void test_loop_loss()
{
    printf("\n%s\n2. 每圈损耗 L(f)：对标参考的 100 Hz-8 kHz 表\n%s\n",
           std::string(84, '=').c_str(), std::string(84, '=').c_str());
    // 参考实测（LP=1.0 列，dB）—— ref_delay_loop_filter.py
    const double freqs[] = { 100, 200, 350, 500, 700, 1000, 1500, 2000,
                             3000, 4000, 5000, 6000, 8000 };
    const double refDb[] = { -0.0396, -0.0445, -0.0391, -0.0783, -0.1123,
                             -0.1761, -0.3391, -0.5636, -1.1800, -1.9995,
                             -3.0179, -4.2793, -8.0913 };
    printf("  %7s %10s %10s %9s\n", "频率", "候选 dB", "参考 dB", "差");
    double worst = 0.0;
    for (int i = 0; i < 13; ++i)
    {
        const double g = loop_gain(freqs[i], 1.0, 1.0, 0.0) / DT::kMeasFeedbackMax;
        const double d = 20.0 * std::log10(g + 1e-30);
        const double diff = d - refDb[i];
        if (std::fabs(diff) > worst) worst = std::fabs(diff);
        printf("  %7.0f %10.4f %10.4f %+9.4f\n", freqs[i], d, refDb[i], diff);
    }
    // 门限 0.25 dB 的来历：闭环校正后的解析拟合是 0.0183 dB，
    // 剩下的差是本估计器在**两侧不完全对称**的残余（8 kHz 上 0.197 dB）。
    // 验收口径是逐 bin ≤3 dB，这里留一个数量级以上的余量即可；
    // 若哪天退化到 0.25 dB 以上，说明常数或机制真的动了。
    printf("\n  最差偏差 = %.4f dB   %s\n", worst,
           worst < 0.25 ? "OK" : "需要调 kFitLoopFixedLp*");
    printf("  参考：闭环校正后的解析拟合最差 0.0183 dB（tools/fit/fit_delay_loop_filter.py）\n");
}

// ------------------------------------------------ 3. 反馈量化
static void test_fb_quant()
{
    printf("\n%s\n3. 反馈量化：同格内应给出完全相同的系数\n%s\n",
           std::string(84, '=').c_str(), std::string(84, '=').c_str());
    const double scan[] = { 0.290, 0.295, 0.300, 0.305, 0.309, 0.311, 0.320, 0.330 };
    printf("  %7s %8s %12s %11s\n", "norm", "格", "coeff", "/首个");
    double first = 0.0;
    for (int i = 0; i < 8; ++i)
    {
        const double c = DT::feedbackFromNorm(scan[i]);
        if (i == 0) first = c;
        printf("  %7.3f %8.0f %12.6f %11.6f\n",
               scan[i], std::floor(scan[i] * 50.0 + 0.5), c, c / first);
    }
    printf("\n  参考实测：0.295/0.300/0.305/0.309 四点读数相同到 0.0002%%，\n");
    printf("  0.311 起跳到 16/15 = 1.066667。\n");
}

int main()
{
    printf("候选侧延迟段自检（对标 docs/REFERENCE.md §14 的参考实测）\n");
    test_fb_quant();
    test_lfo_depth();
    test_loop_loss();
    return 0;
}
