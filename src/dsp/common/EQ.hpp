/*
  ==============================================================================

    EQ.h
    Created: 16 Apr 2024 10:58:21am
    Author:  Dell

  ==============================================================================
*/

#ifndef EQ_HPP
#define EQ_HPP
#include "NeoGlobal.hpp"
#include "VectorOpsComplex.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <complex>
#include <iterator>
#include <limits>
#include <memory>
#include <numeric>
#include <thread>
#include <vector>
#if defined(__aarch64__)
#include <arm_neon.h>
#endif

// M_PI 不是标准 C++，MSVC / Windows 上的 clang 默认不提供（需要在包含 <cmath>
// 之前定义 _USE_MATH_DEFINES）。这里做一次可移植的兜底定义。
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace biquad {
template <typename T>
inline std::array<T, 5> single_allpass(T frequency) noexcept {
	T K = std::tan(T(M_PI) * frequency / T(2));
	return {(K - 1.0f) / (K + 1.0f), 1.0f, 0.0f, -(K - 1.0f) / (K + 1.0f), 0.0f};
}
template <typename T>
inline std::array<T, 5> single_lowpass(T frequency) noexcept {
	T K = std::tan(T(M_PI) * frequency / T(2));
	return {K / (K + 1.0f), K / (K + 1.0f), 0.0f, -(K - 1.0f) / (K + 1.0f), 0.0f};
}
template <typename T>
inline std::array<T, 5> single_highpass(T frequency) noexcept {
	T K = std::tan(T(M_PI) * frequency / T(2));
	return {1.0f / (K + 1.0f), -1.0f / (K + 1.0f), 0.0f, -(K - 1.0f) / (K + 1.0f), 0.0f};
}

template <typename T>
inline std::array<T, 5> single_tilt(T frequency, T gain) noexcept {
	const T gfactor = 4, amp = M_PI;
	T g1, g2;
	if (gain > 0) {
		g1 = -gfactor * gain;
		g2 = gain;
	} else {
		g1 = -gain;
		g2 = gfactor * gain;
	}
	T lgain = exp(g1 / amp) - 1;
	T hgain = exp(g2 / amp) - 1;
	T omega = 2 * M_PI * frequency;
	T sr3 = M_PI * 2;
	T n = 1 / (sr3 + omega);
	T a0 = 2 * omega * n;
	T b1 = (sr3 - omega) * n;
	std::array<T, 5> ret;
	ret[0] = (1 + hgain) + (lgain - hgain) * a0;
	ret[1] = -(1 + hgain) * b1;
	ret[2] = 0;
	ret[3] = b1;
	ret[4] = 0;
	return ret;
}

template <typename T>
inline std::array<T, 5> biquad_allpass(T frequency, T Q) noexcept {
	T K = std::tan(T(M_PI) * frequency / T(2));
	return {
	    (K * K * Q - K + Q) / (K * K * Q + K + Q), 2.0f * Q * (K * K - 1.0f) / (K * K * Q + K + Q), 1.0f, -2.0f * Q * (K * K - 1.0f) / (K * K * Q + K + Q), -(K * K * Q - K + Q) / (K * K * Q + K + Q)};
}
template <typename T>
inline std::array<T, 5> biquad_lowpass(T frequency, T Q) noexcept {
	T K = std::tan(T(M_PI) / T(2) * frequency);
	Q = Q / sqrt2_v;
	T K2 = K * K;
	T norm = (Q + K + K2 * Q);
	T a0 = K2 * Q / norm;
	T a1 = 2.0f * a0;
	T a2 = a0;
	T b1 = 2.0f * (K2 - 1.0f) * Q / norm;
	T b2 = (Q - K + K2 * Q) / norm;
	return {a0, a1, a2, -b1, -b2};
}
template <typename T>
inline std::array<T, 5> biquad_highpass(T frequency, T Q = 1) noexcept {
	Q /= sqrt2_v;
	T K = std::tan(T(M_PI) / T(2) * frequency);
	T K2 = K * K;
	T norm = (Q + K + K2 * Q);
	T a0 = Q / norm;
	T a1 = -2.0f * a0;
	T a2 = a0;
	T b1 = 2.0f * (K2 - 1.0f) * Q / norm;
	T b2 = (Q - K + K2 * Q) / norm;
	return {a0, a1, a2, -b1, -b2};
}
template <typename T>
inline std::array<T, 5> biquad_bandpass(T frequency, T Q) noexcept {
	T K = std::tan(T(M_PI) * frequency / T(2));
	T K2 = K * K;
	T norm = 1 / (1 + K / Q + K2);
	T a0 = K / Q * norm;
	T a1 = 0;
	T a2 = -a0;
	T b1 = 2 * (K2 - 1) * norm;
	T b2 = (1 - K / Q + K2) * norm;
	return {a0, a1, a2, -b1, -b2};
}
template <typename T>
inline std::array<T, 5> biquad_notch(T frequency, T Q) noexcept {
	T K = tan((float)M_PI * frequency / 2);
	T K2 = K * K;
	T norm = 1 / (1 + K / Q + K2);
	T a0 = (1 + K2) * norm;
	T a1 = 2 * (K2 - 1) * norm;
	T a2 = a0;
	T b1 = a1;
	T b2 = (1 - K / Q + K2) * norm;
	return {a0, a1, a2, -b1, -b2};
}
template <typename T>
inline std::array<T, 5> biquad_peak(T frequency, T gain, T Q) noexcept {
	T K = tan((float)M_PI * frequency / 2);
	T K2 = K * K;
	const T log10 = static_cast<T>(2.3025850929940456840179914546844f);
	T V = exp(abs(gain) * (1.0f / 20.0f) * log10);
	T norm, a0, a1, a2, b1, b2;
	if (gain >= 0) {
		norm = 1 / (1 + 1 / Q * K + K2);
		a0 = (1 + V / Q * K + K2) * norm;
		a1 = 2 * (K2 - 1) * norm;
		a2 = (1 - V / Q * K + K2) * norm;
		b1 = a1;
		b2 = (1 - 1 / Q * K + K2) * norm;
	} else {
		norm = 1 / (1 + V / Q * K + K2);
		a0 = (1 + 1 / Q * K + K2) * norm;
		a1 = 2 * (K2 - 1) * norm;
		a2 = (1 - 1 / Q * K + K2) * norm;
		b1 = a1;
		b2 = (1 - V / Q * K + K2) * norm;
	}

	return {a0, a1, a2, -b1, -b2};
}
template <typename T>
inline std::array<T, 5> biquad_lowshelf(T frequency, T gain) noexcept {
	static_assert(std::is_floating_point_v<T>, "T must be a floating-point type");
	T K = tan((float)M_PI * frequency / 2);
	T K2 = K * K;
	const T log10 = static_cast<T>(2.3025850929940456840179914546844);
	const T log2 = static_cast<T>(0.69314718055994530941723212145818);
	const T sqrt2 = static_cast<T>(1.4142135623730950488016887242097);
	T V = exp(abs(gain) * (1.0f / 20.0f) * log10);
	T sqrtV = exp(abs(gain) * (1.0f / 20.0f) * log10 / 2);
	T norm, a0, a1, a2, b1, b2;
	if (gain >= 0) {
		norm = 1 / (1 + sqrt2 * K + K2);
		a0 = (1 + sqrtV * sqrt2 * K + V * K2) * norm;
		a1 = 2 * (V * K2 - 1) * norm;
		a2 = (1 - sqrtV * sqrt2 * K + V * K2) * norm;
		b1 = 2 * (K2 - 1) * norm;
		b2 = (1 - sqrt2 * K + K2) * norm;
	} else {
		norm = 1 / (1 + sqrtV * sqrt2 * K + V * K2);
		a0 = (1 + sqrt2 * K + K2) * norm;
		a1 = 2 * (K2 - 1) * norm;
		a2 = (1 - sqrt2 * K + K2) * norm;
		b1 = 2 * (V * K2 - 1) * norm;
		b2 = (1 - sqrtV * sqrt2 * K + V * K2) * norm;
	}

	return {a0, a1, a2, -b1, -b2};
}
template <typename T>
inline std::array<T, 5> biquad_highshelf(T frequency, T gain) noexcept {
	T K = tan((float)M_PI * frequency / 2);
	T K2 = K * K;
	const T log10 = static_cast<T>(2.3025850929940456840179914546844f);
	T V = exp(abs(gain) * (1.0f / 20.0f) * log10);
	T norm, a0, a1, a2, b1, b2;
	if (gain >= 0) {
		norm = 1 / (1 + sqrt(2) * K + K2);
		a0 = (V + sqrt(2 * V) * K + K2) * norm;
		a1 = 2 * (K2 - V) * norm;
		a2 = (V - sqrt(2 * V) * K + K2) * norm;
		b1 = 2 * (K2 - 1) * norm;
		b2 = (1 - sqrt(2) * K + K2) * norm;
	} else {
		norm = 1 / (V + sqrt(2 * V) * K + K2);
		a0 = (1 + sqrt(2) * K + K2) * norm;
		a1 = 2 * (K2 - 1) * norm;
		a2 = (1 - sqrt(2) * K + K2) * norm;
		b1 = 2 * (K2 - V) * norm;
		b2 = (V - sqrt(2 * V) * K + K2) * norm;
	}

	return {a0, a1, a2, -b1, -b2};
}

template <typename T>
inline std::array<T, 5> biquad_lowshelf_withQ(T frequency, T gain, T Q = 0.707) noexcept {
	static_assert(std::is_floating_point_v<T>, "T must be a floating-point type");
	T A = exp(gain * (1.0f / 20.0f) * ln10_v / 2);
	T w0 = 2 * M_PI * frequency / 2;
	T alpha = sin(w0) / (2 * Q);

	T a0 = A * ((A + 1) - (A - 1) * cos(w0) + 2 * sqrt(A) * alpha);
	T a1 = 2 * A * ((A - 1) - (A + 1) * cos(w0));
	T a2 = A * ((A + 1) - (A - 1) * cos(w0) - 2 * sqrt(A) * alpha);
	T b0 = (A + 1) + (A - 1) * cos(w0) + 2 * sqrt(A) * alpha;
	T b1 = -2 * ((A - 1) + (A + 1) * cos(w0));
	T b2 = (A + 1) + (A - 1) * cos(w0) - 2 * sqrt(A) * alpha;
	return {a0 / b0, a1 / b0, a2 / b0, -b1 / b0, -b2 / b0};
}
template <typename T>
inline std::array<T, 5> biquad_highshelf_withQ(T frequency, T gain, T Q = 0.707) noexcept {
	static_assert(std::is_floating_point_v<T>, "T must be a floating-point type");
	T A = exp(gain * (1.0f / 20.0f) * ln10_v / 2);
	T w0 = 2 * M_PI * frequency / 2;
	T alpha = sin(w0) / (2 * Q);

	T a0 = A * ((A + 1) + (A - 1) * cos(w0) + 2 * sqrt(A) * alpha);
	T a1 = -2 * A * ((A - 1) + (A + 1) * cos(w0));
	T a2 = A * ((A + 1) + (A - 1) * cos(w0) - 2 * sqrt(A) * alpha);
	T b0 = (A + 1) - (A - 1) * cos(w0) + 2 * sqrt(A) * alpha;
	T b1 = 2 * ((A - 1) - (A + 1) * cos(w0));
	T b2 = (A + 1) - (A - 1) * cos(w0) - 2 * sqrt(A) * alpha;
	return {a0 / b0, a1 / b0, a2 / b0, -b1 / b0, -b2 / b0};
}

template <typename T>
inline std::vector<std::array<T, 5>> bibiquad_tiltshelf(T frequency, T gain) noexcept {
	return {biquad_lowshelf(frequency, gain), biquad_highshelf(frequency, -gain)};
}
template <typename T, typename S = T>
std::array<S, 5> single_combine(const std::array<T, 5> &a, const std::array<T, 5> &b) noexcept {
	return {static_cast<S>(a[0] * b[0]), static_cast<S>(a[1] * b[0] + a[0] * b[1]), static_cast<S>(a[1] * b[1]), static_cast<S>(a[3] + b[3]), static_cast<S>(-a[3] * b[3])};
}
} // namespace biquad

template <typename T>
struct zpk {
	std::vector<std::complex<T>> zeros;
	std::vector<std::complex<T>> poles;
	double gain = 1.0;
};

namespace ZpkFilter {

// 对实数类型的共轭操作（实数的共轭就是其本身）
template <typename T>
typename std::enable_if<std::is_arithmetic<T>::value, T>::type cconj(const T &x) {
	return x;
}

// 对复数类型的共轭操作
template <typename T>
std::complex<T> cconj(const std::complex<T> &x) {
	return std::conj(x);
}

// 对复数向量的共轭操作（如果需要处理向量的情况）
template <typename T>
std::vector<std::complex<T>> cconj(const std::vector<std::complex<T>> &x) {
	std::vector<std::complex<T>> result;
	result.reserve(x.size());

	for (const auto &val : x) { result.push_back(std::conj(val)); }

	return result;
}

template <typename T>
zpk<T> chebyshev1(int N, T rp) {
	if (N <= 0) { return {{}, {}, T(1)}; }

	const T pi = T(3.14159265358979323846);

	// Calculate epsilon
	T eps = std::sqrt(std::pow(T(10), T(0.1) * rp) - T(1.0));
	T mu = T(1.0) / N * std::asinh(T(1.0) / eps);

	// Generate m values
	std::vector<T, std::allocator<T>> m(N);
	for (int i = 0; i < N; ++i) { m[i] = -N + 1 + 2 * i; }

	// Calculate theta values
	std::vector<T, std::allocator<T>> theta(N);
	for (int i = 0; i < N; ++i) { theta[i] = pi * m[i] / (T(2) * N); }

	// Calculate poles
	std::vector<std::complex<T>> p(N);
	for (int i = 0; i < N; ++i) {
		std::complex<T> z(mu, theta[i]);
		T sinh_re = std::sinh(z.real()) * std::cos(z.imag());
		T sinh_im = std::cosh(z.real()) * std::sin(z.imag());
		p[i] = std::complex<T>(-sinh_re, -sinh_im);
	}

	// Calculate gain (k)
	std::complex<T> k(T(1), T(0));
	for (const auto &pole : p) { k *= -pole; }
	T k_real = k.real();

	if (N % 2 == 0) { k_real /= std::sqrt(T(1.0) + eps * eps); }

	return {{}, std::move(p), k_real};
}

template <typename T>
zpk<T> chebyshev2(int N, T rs) {
	if (N <= 0) { return {{}, {}, T(1)}; }
	constexpr T pi = T(3.14159265358979323846);

	// Calculate de and mu
	// T de = T(1.0) / std::sqrt(exp(ln10_v*(0.1*rs)) - T(1));
	T mu = std::asinh(std::sqrt(exp(ln10_v * (0.1 * rs)) - T(1))) / N;

	// Generate m values
	std::vector<T, std::allocator<T>> m;
	if (N % 2) {
		// Odd N case
		int half = N / 2;
		m.reserve(N);

		// First half
		for (int i = 0; i < half; ++i) { m.push_back(-N + 1 + i * 2); }
		// Second half
		for (int i = 0; i < half; ++i) { m.push_back(2 + i * 2); }
	} else {
		// Even N case
		m.resize(N);
		for (int i = 0; i < N; ++i) { m[i] = -N + 1 + 2 * i; }
	}

	// Calculate zeros
	std::vector<std::complex<T>, std::allocator<std::complex<T>>> z(m.size());
	for (size_t i = 0; i < m.size(); ++i) {
		T angle = m[i] * pi / (T(2.0) * N);
		z[i] = std::complex<T>(T(0), T(1.0) / std::sin(angle));
	}

	// Calculate poles
	std::vector<std::complex<T>> p(N);
	for (int i = 0; i < N; ++i) {
		T angle = pi * (-N + 1 + 2 * i) / (T(2.0) * N);
		T real_part = std::cos(angle);
		T imag_part = std::sin(angle);

		// Apply sinh and cosh transformations
		T transformed_real = std::sinh(mu) * real_part;
		T transformed_imag = std::cosh(mu) * imag_part;

		// Calculate reciprocal
		T denom = transformed_real * transformed_real + transformed_imag * transformed_imag;
		p[i] = std::complex<T>(-transformed_real / denom, -transformed_imag / denom);
	}

	// Calculate gain
	std::complex<T> k(T(1), T(0));
	for (const auto &pole : p) { k *= -pole; }
	for (const auto &zero : z) { k /= -zero; }
	T k_real = k.real();

	return {std::move(z), std::move(p), k_real};
}

// ============================================================
// 椭圆（Cauer）模拟低通原型：等价于 scipy.signal.ellipap(N, rp, rs)。
// 返回归一化原型（角频率截止 1 rad/s），可与 chebyshev1/chebyshev2/butterworth
// 复用完全相同的设计流程：
//     to_sos(iir_lowpass(elliptic<float>(N, rp, rs), fc), 0)  ->  MultiStateFilters
// 椭圆滤波器在通带与阻带均带波纹，可在极低阶数下得到最陡的过渡带。
// 内部一律用 double 计算椭圆积分与 Jacobi 椭圆函数以保证数值精度，最终转回 T。
// 算法参考 Orfanidis, "Lecture Notes on Elliptic Filter Design"。
// ============================================================
namespace elliptic_detail {

// 完全椭圆积分 K(m)（参数 m = k^2），算术-几何平均（AGM）迭代。
inline double ellipk(double m) {
	if (m >= 1.0) return std::numeric_limits<double>::infinity();
	double a = 1.0, b = std::sqrt(1.0 - m);
	for (int i = 0; i < 64 && std::abs(a - b) > 1e-16 * a; ++i) {
		const double an = 0.5 * (a + b);
		b = std::sqrt(a * b);
		a = an;
	}
	return M_PI / (a + b); // = pi / (2 * AGM(1, sqrt(1-m)))
}
inline double ellipkm1(double p) { return ellipk(1.0 - p); }

// 10^x - 1，x 接近 0 时保持精度。
inline double pow10m1(double x) {
	return std::expm1(2.302585092994045684017991454684 * x); // ln(10) * x
}

// Jacobi 椭圆函数 sn/cn/dn（实参 u，参数 m∈[0,1]），Cephes ellpj 算法。
inline void ellipj(double u, double m, double &sn, double &cn, double &dn) {
	constexpr double MACHEP = 1.11022302462515654042e-16;
	if (m < 1.0e-9) {
		const double t = std::sin(u), b = std::cos(u);
		const double ai = 0.25 * m * (u - t * b);
		sn = t - ai * b;
		cn = b + ai * t;
		dn = 1.0 - 0.5 * m * t * t;
		return;
	}
	if (m >= 0.9999999999) {
		const double ai = 0.25 * (1.0 - m);
		const double b = std::cosh(u), t = std::tanh(u);
		const double phi = 1.0 / b, twon = b * std::sinh(u);
		sn = t + ai * (twon - u) / (b * b);
		const double aiph = ai * t * phi;
		cn = phi - aiph * (twon - u);
		dn = phi + aiph * (twon + u);
		return;
	}
	double a[9], c[9];
	a[0] = 1.0;
	double b = std::sqrt(1.0 - m);
	c[0] = std::sqrt(m);
	double twon = 1.0;
	int i = 0;
	while (std::abs(c[i] / a[i]) > MACHEP && i < 8) {
		const double ai = a[i];
		++i;
		c[i] = 0.5 * (ai - b);
		const double t = std::sqrt(ai * b);
		a[i] = 0.5 * (ai + b);
		b = t;
		twon *= 2.0;
	}
	double phi = twon * a[i] * u, bprev;
	do {
		const double t = c[i] * std::sin(phi) / a[i];
		bprev = phi;
		phi = 0.5 * (std::asin(t) + phi);
	} while (--i);
	sn = std::sin(phi);
	const double cphi = std::cos(phi);
	cn = cphi;
	const double dnfac = std::cos(phi - bprev);
	// DLMF 22.20.5 附近讨论：dnfac 接近 0 时改用恒等式，避免除法放大误差。
	dn = (std::abs(dnfac) < 0.1) ? std::sqrt(1.0 - m * sn * sn) : cphi / dnfac;
}

// 逆 Jacobi sn（复参 w，实参 m），下降 Landen 变换。
inline std::complex<double> arc_jac_sn(std::complex<double> w, double m) {
	const double k = std::sqrt(m);
	if (k > 1.0) return {std::nan(""), std::nan("")};
	if (k == 1.0) return std::atanh(w);

	double ks[16];
	int nk = 0;
	ks[nk++] = k;
	while (ks[nk - 1] != 0.0 && nk < 16) {
		const double kk = ks[nk - 1];
		const double kp = std::sqrt((1.0 - kk) * (1.0 + kk));
		ks[nk++] = (1.0 - kp) / (1.0 + kp);
	}
	double K = M_PI * 0.5;
	for (int i = 1; i < nk; ++i) K *= (1.0 + ks[i]);

	std::complex<double> wn = w;
	for (int i = 0; i + 1 < nk; ++i) {
		const std::complex<double> t = ks[i] * wn;
		const std::complex<double> comp = std::sqrt((1.0 - t) * (1.0 + t));
		wn = 2.0 * wn / ((1.0 + ks[i + 1]) * (1.0 + comp));
	}
	return K * (2.0 / M_PI) * std::asin(wn);
}

// 逆 Jacobi sc（实参 w，实参 m），返回实数（sc(z,1-m) = -i*sn(i*z,m) 的虚部）。
inline double arc_jac_sc1(double w, double m) {
	return arc_jac_sn(std::complex<double>(0.0, w), m).imag();
}

// 度数方程：给定 n、m1，用 nome 级数求 m。
inline double ellipdeg(int n, double m1) {
	constexpr int MMAX = 7;
	const double K1 = ellipk(m1), K1p = ellipkm1(m1);
	const double q1 = std::exp(-M_PI * K1p / K1);
	const double q = std::pow(q1, 1.0 / n);
	double num = 0.0, den = 1.0;
	for (int i = 0; i <= MMAX; ++i) num += std::pow(q, (double)(i * (i + 1)));
	for (int i = 1; i <= MMAX + 1; ++i) den += 2.0 * std::pow(q, (double)(i * i));
	const double r = num / den;
	return 16.0 * q * (r * r) * (r * r);
}

} // namespace elliptic_detail

template <typename T>
zpk<T> elliptic(int N, T rp, T rs) {
	using namespace elliptic_detail;
	if (N <= 0) { return {{}, {}, std::pow(10.0, -static_cast<double>(rp) / 20.0)}; }
	if (N == 1) {
		const double pr = -std::sqrt(1.0 / pow10m1(0.1 * static_cast<double>(rp)));
		return {{}, {std::complex<T>(static_cast<T>(pr), T(0))}, -pr};
	}

	constexpr double EPS = 2e-16;
	const double eps_sq = pow10m1(0.1 * static_cast<double>(rp));
	const double eps    = std::sqrt(eps_sq);
	const double ck1_sq = eps_sq / pow10m1(0.1 * static_cast<double>(rs));

	const double val0 = ellipk(ck1_sq);
	const double m     = ellipdeg(N, ck1_sq);
	const double capk  = ellipk(m);
	const double sqrtm = std::sqrt(m);

	// 每个 s 分量对应一对纯虚共轭零点；s、c、d 缓存下来供极点计算复用。
	std::vector<double> sVals, cVals, dVals;
	sVals.reserve(static_cast<size_t>(N));
	cVals.reserve(static_cast<size_t>(N));
	dVals.reserve(static_cast<size_t>(N));
	std::vector<std::complex<T>> zeros;
	for (int j = 1 - (N % 2); j < N; j += 2) {
		double s, c, d;
		ellipj(static_cast<double>(j) * capk / N, m, s, c, d);
		sVals.push_back(s);
		cVals.push_back(c);
		dVals.push_back(d);
		if (std::abs(s) > EPS) {
			const double zi = 1.0 / (sqrtm * s); // z = 1j / (sqrt(m) * s)
			zeros.emplace_back(T(0), static_cast<T>(zi));
			zeros.emplace_back(T(0), static_cast<T>(-zi));
		}
	}

	const double r  = arc_jac_sc1(1.0 / eps, ck1_sq);
	const double v0 = capk * r / (N * val0);
	double sv, cv, dv;
	ellipj(v0, 1.0 - m, sv, cv, dv);

	std::vector<std::complex<double>> poles;
	poles.reserve(static_cast<size_t>(N));
	for (size_t i = 0; i < sVals.size(); ++i) {
		const double s = sVals[i], c = cVals[i], d = dVals[i];
		const double den = 1.0 - (d * sv) * (d * sv);
		// p = -(c*d*sv*cv + 1j*s*dv) / (1 - (d*sv)^2)
		poles.emplace_back(-(c * d * sv * cv) / den, -(s * dv) / den);
	}
	if (N % 2) {
		// 奇数阶：中间的实极点不复制，仅对复极点补共轭。
		double sumsq = 0.0;
		for (const auto &pp : poles) sumsq += std::norm(pp);
		const double thr = EPS * std::sqrt(sumsq);
		const size_t n0 = poles.size();
		for (size_t i = 0; i < n0; ++i)
			if (std::abs(poles[i].imag()) > thr) poles.push_back(std::conj(poles[i]));
	} else {
		const size_t n0 = poles.size();
		for (size_t i = 0; i < n0; ++i) poles.push_back(std::conj(poles[i]));
	}

	std::complex<double> prodP(1, 0), prodZ(1, 0);
	for (const auto &pp : poles) prodP *= -pp;
	for (const auto &zz : zeros)
		prodZ *= std::complex<double>(-static_cast<double>(zz.real()), -static_cast<double>(zz.imag()));
	double k = (prodP / prodZ).real();
	if (N % 2 == 0) k /= std::sqrt(1.0 + eps_sq);

	zpk<T> out;
	out.zeros = std::move(zeros);
	out.poles.reserve(poles.size());
	for (const auto &pp : poles)
		out.poles.emplace_back(static_cast<T>(pp.real()), static_cast<T>(pp.imag()));
	out.gain = k;
	return out;
}

template <typename T>
zpk<T> butterworth(int N) {
	switch (N) {
	case 1: return {{}, {std::complex<T>(-1.0, -0.0)}, 1.0};
	case 2: return {{}, {std::complex<T>(-0.7071067811865476, 0.7071067811865476), std::complex<T>(-0.7071067811865476, -0.7071067811865476)}, 1.0};
	case 3: return {{}, {std::complex<T>(-0.5000000000000001, 0.8660254037844386), std::complex<T>(-1.0, -0.0), std::complex<T>(-0.5000000000000001, -0.8660254037844386)}, 1.0};
	case 4:
		return {{},
		    {std::complex<T>(-0.38268343236508984, 0.9238795325112867), std::complex<T>(-0.9238795325112867, 0.3826834323650898), std::complex<T>(-0.9238795325112867, -0.3826834323650898),
		        std::complex<T>(-0.38268343236508984, -0.9238795325112867)},
		    1.0};
	case 5:
		return {{},
		    {std::complex<T>(-0.30901699437494745, 0.9510565162951535), std::complex<T>(-0.8090169943749475, 0.5877852522924731), std::complex<T>(-1.0, -0.0),
		        std::complex<T>(-0.8090169943749475, -0.5877852522924731), std::complex<T>(-0.30901699437494745, -0.9510565162951535)},
		    1.0};
	case 6:
		return {{},
		    {std::complex<T>(-0.25881904510252096, 0.9659258262890682), std::complex<T>(-0.7071067811865476, 0.7071067811865476), std::complex<T>(-0.9659258262890683, 0.25881904510252074),
		        std::complex<T>(-0.9659258262890683, -0.25881904510252074), std::complex<T>(-0.7071067811865476, -0.7071067811865476), std::complex<T>(-0.25881904510252096, -0.9659258262890682)},
		    1.0};
	case 7:
		return {{},
		    {std::complex<T>(-0.22252093395631445, 0.9749279121818236), std::complex<T>(-0.6234898018587336, 0.7818314824680298), std::complex<T>(-0.9009688679024191, 0.4338837391175581),
		        std::complex<T>(-1.0, -0.0), std::complex<T>(-0.9009688679024191, -0.4338837391175581), std::complex<T>(-0.6234898018587336, -0.7818314824680298),
		        std::complex<T>(-0.22252093395631445, -0.9749279121818236)},
		    1.0};
	case 8:
		return {{},
		    {std::complex<T>(-0.19509032201612833, 0.9807852804032304), std::complex<T>(-0.5555702330196023, 0.8314696123025452), std::complex<T>(-0.8314696123025452, 0.5555702330196022),
		        std::complex<T>(-0.9807852804032304, 0.19509032201612825), std::complex<T>(-0.9807852804032304, -0.19509032201612825), std::complex<T>(-0.8314696123025452, -0.5555702330196022),
		        std::complex<T>(-0.5555702330196023, -0.8314696123025452), std::complex<T>(-0.19509032201612833, -0.9807852804032304)},
		    1.0};
	case 9:
		return {{},
		    {std::complex<T>(-0.17364817766693041, 0.984807753012208), std::complex<T>(-0.5000000000000001, 0.8660254037844386), std::complex<T>(-0.766044443118978, 0.6427876096865393),
		        std::complex<T>(-0.9396926207859084, 0.3420201433256687), std::complex<T>(-1.0, -0.0), std::complex<T>(-0.9396926207859084, -0.3420201433256687),
		        std::complex<T>(-0.766044443118978, -0.6427876096865393), std::complex<T>(-0.5000000000000001, -0.8660254037844386), std::complex<T>(-0.17364817766693041, -0.984807753012208)},
		    1.0};
	case 10:
		return {{},
		    {std::complex<T>(-0.15643446504023092, 0.9876883405951378), std::complex<T>(-0.4539904997395468, 0.8910065241883678), std::complex<T>(-0.7071067811865476, 0.7071067811865476),
		        std::complex<T>(-0.8910065241883679, 0.45399049973954675), std::complex<T>(-0.9876883405951378, 0.15643446504023087), std::complex<T>(-0.9876883405951378, -0.15643446504023087),
		        std::complex<T>(-0.8910065241883679, -0.45399049973954675), std::complex<T>(-0.7071067811865476, -0.7071067811865476), std::complex<T>(-0.4539904997395468, -0.8910065241883678),
		        std::complex<T>(-0.15643446504023092, -0.9876883405951378)},
		    1.0};
	case 11:
		return {{},
		    {std::complex<T>(-0.14231483827328512, 0.9898214418809327), std::complex<T>(-0.41541501300188644, 0.9096319953545183), std::complex<T>(-0.654860733945285, 0.7557495743542583),
		        std::complex<T>(-0.8412535328311812, 0.5406408174555976), std::complex<T>(-0.9594929736144974, 0.28173255684142967), std::complex<T>(-1.0, -0.0),
		        std::complex<T>(-0.9594929736144974, -0.28173255684142967), std::complex<T>(-0.8412535328311812, -0.5406408174555976), std::complex<T>(-0.654860733945285, -0.7557495743542583),
		        std::complex<T>(-0.41541501300188644, -0.9096319953545183), std::complex<T>(-0.14231483827328512, -0.9898214418809327)},
		    1.0};
	case 12:
		return {{},
		    {std::complex<T>(-0.13052619222005193, 0.9914448613738104), std::complex<T>(-0.38268343236508984, 0.9238795325112867), std::complex<T>(-0.6087614290087207, 0.7933533402912352),
		        std::complex<T>(-0.7933533402912353, 0.6087614290087205), std::complex<T>(-0.9238795325112867, 0.3826834323650898), std::complex<T>(-0.9914448613738104, 0.13052619222005157),
		        std::complex<T>(-0.9914448613738104, -0.13052619222005157), std::complex<T>(-0.9238795325112867, -0.3826834323650898), std::complex<T>(-0.7933533402912353, -0.6087614290087205),
		        std::complex<T>(-0.6087614290087207, -0.7933533402912352), std::complex<T>(-0.38268343236508984, -0.9238795325112867), std::complex<T>(-0.13052619222005193, -0.9914448613738104)},
		    1.0};
	case 13:
		return {{},
		    {std::complex<T>(-0.120536680255323, 0.992708874098054), std::complex<T>(-0.35460488704253557, 0.9350162426854148), std::complex<T>(-0.5680647467311558, 0.8229838658936565),
		        std::complex<T>(-0.7485107481711011, 0.6631226582407952), std::complex<T>(-0.8854560256532099, 0.46472317204376856), std::complex<T>(-0.970941817426052, 0.23931566428755777),
		        std::complex<T>(-1.0, -0.0), std::complex<T>(-0.970941817426052, -0.23931566428755777), std::complex<T>(-0.8854560256532099, -0.46472317204376856),
		        std::complex<T>(-0.7485107481711011, -0.6631226582407952), std::complex<T>(-0.5680647467311558, -0.8229838658936565), std::complex<T>(-0.35460488704253557, -0.9350162426854148),
		        std::complex<T>(-0.120536680255323, -0.992708874098054)},
		    1.0};
	case 14:
		return {{},
		    {std::complex<T>(-0.11196447610330791, 0.9937122098932426), std::complex<T>(-0.3302790619551673, 0.9438833303083675), std::complex<T>(-0.5320320765153366, 0.8467241992282841),
		        std::complex<T>(-0.7071067811865476, 0.7071067811865476), std::complex<T>(-0.8467241992282842, 0.5320320765153366), std::complex<T>(-0.9438833303083676, 0.3302790619551671),
		        std::complex<T>(-0.9937122098932426, 0.11196447610330786), std::complex<T>(-0.9937122098932426, -0.11196447610330786), std::complex<T>(-0.9438833303083676, -0.3302790619551671),
		        std::complex<T>(-0.8467241992282842, -0.5320320765153366), std::complex<T>(-0.7071067811865476, -0.7071067811865476), std::complex<T>(-0.5320320765153366, -0.8467241992282841),
		        std::complex<T>(-0.3302790619551673, -0.9438833303083675), std::complex<T>(-0.11196447610330791, -0.9937122098932426)},
		    1.0};
	case 15:
		return {{},
		    {std::complex<T>(-0.10452846326765346, 0.9945218953682733), std::complex<T>(-0.30901699437494745, 0.9510565162951535), std::complex<T>(-0.5000000000000001, 0.8660254037844386),
		        std::complex<T>(-0.6691306063588582, 0.7431448254773941), std::complex<T>(-0.8090169943749475, 0.5877852522924731), std::complex<T>(-0.9135454576426009, 0.40673664307580015),
		        std::complex<T>(-0.9781476007338057, 0.20791169081775931), std::complex<T>(-1.0, -0.0), std::complex<T>(-0.9781476007338057, -0.20791169081775931),
		        std::complex<T>(-0.9135454576426009, -0.40673664307580015), std::complex<T>(-0.8090169943749475, -0.5877852522924731), std::complex<T>(-0.6691306063588582, -0.7431448254773941),
		        std::complex<T>(-0.5000000000000001, -0.8660254037844386), std::complex<T>(-0.30901699437494745, -0.9510565162951535), std::complex<T>(-0.10452846326765346, -0.9945218953682733)},
		    1.0};
	case 16:
		return {{},
		    {std::complex<T>(-0.09801714032956077, 0.9951847266721968), std::complex<T>(-0.29028467725446233, 0.9569403357322089), std::complex<T>(-0.4713967368259978, 0.8819212643483549),
		        std::complex<T>(-0.6343932841636455, 0.7730104533627369), std::complex<T>(-0.773010453362737, 0.6343932841636455), std::complex<T>(-0.881921264348355, 0.47139673682599764),
		        std::complex<T>(-0.9569403357322088, 0.29028467725446233), std::complex<T>(-0.9951847266721969, 0.0980171403295606), std::complex<T>(-0.9951847266721969, -0.0980171403295606),
		        std::complex<T>(-0.9569403357322088, -0.29028467725446233), std::complex<T>(-0.881921264348355, -0.47139673682599764), std::complex<T>(-0.773010453362737, -0.6343932841636455),
		        std::complex<T>(-0.6343932841636455, -0.7730104533627369), std::complex<T>(-0.4713967368259978, -0.8819212643483549), std::complex<T>(-0.29028467725446233, -0.9569403357322089),
		        std::complex<T>(-0.09801714032956077, -0.9951847266721968)},
		    1.0};
	case 17:
		return {{},
		    {std::complex<T>(-0.09226835946330202, 0.9957341762950345), std::complex<T>(-0.273662990072083, 0.961825643172819), std::complex<T>(-0.4457383557765383, 0.8951632913550623),
		        std::complex<T>(-0.6026346363792565, 0.7980172272802395), std::complex<T>(-0.7390089172206591, 0.6736956436465572), std::complex<T>(-0.8502171357296142, 0.5264321628773557),
		        std::complex<T>(-0.9324722294043558, 0.3612416661871529), std::complex<T>(-0.9829730996839018, 0.18374951781657034), std::complex<T>(-1.0, -0.0),
		        std::complex<T>(-0.9829730996839018, -0.18374951781657034), std::complex<T>(-0.9324722294043558, -0.3612416661871529), std::complex<T>(-0.8502171357296142, -0.5264321628773557),
		        std::complex<T>(-0.7390089172206591, -0.6736956436465572), std::complex<T>(-0.6026346363792565, -0.7980172272802395), std::complex<T>(-0.4457383557765383, -0.8951632913550623),
		        std::complex<T>(-0.273662990072083, -0.961825643172819), std::complex<T>(-0.09226835946330202, -0.9957341762950345)},
		    1.0};
	case 18:
		return {{},
		    {std::complex<T>(-0.08715574274765814, 0.9961946980917455), std::complex<T>(-0.25881904510252096, 0.9659258262890682), std::complex<T>(-0.42261826174069944, 0.9063077870366499),
		        std::complex<T>(-0.5735764363510463, 0.8191520442889917), std::complex<T>(-0.7071067811865476, 0.7071067811865476), std::complex<T>(-0.8191520442889918, 0.573576436351046),
		        std::complex<T>(-0.9063077870366499, 0.4226182617406994), std::complex<T>(-0.9659258262890683, 0.25881904510252074), std::complex<T>(-0.9961946980917455, 0.08715574274765817),
		        std::complex<T>(-0.9961946980917455, -0.08715574274765817), std::complex<T>(-0.9659258262890683, -0.25881904510252074), std::complex<T>(-0.9063077870366499, -0.4226182617406994),
		        std::complex<T>(-0.8191520442889918, -0.573576436351046), std::complex<T>(-0.7071067811865476, -0.7071067811865476), std::complex<T>(-0.5735764363510463, -0.8191520442889917),
		        std::complex<T>(-0.42261826174069944, -0.9063077870366499), std::complex<T>(-0.25881904510252096, -0.9659258262890682), std::complex<T>(-0.08715574274765814, -0.9961946980917455)},
		    1.0};
	case 19:
		return {{},
		    {std::complex<T>(-0.0825793454723324, 0.9965844930066698), std::complex<T>(-0.24548548714079924, 0.9694002659393304), std::complex<T>(-0.40169542465296953, 0.9157733266550574),
		        std::complex<T>(-0.5469481581224269, 0.8371664782625285), std::complex<T>(-0.6772815716257411, 0.7357239106731316), std::complex<T>(-0.7891405093963936, 0.6142127126896678),
		        std::complex<T>(-0.8794737512064891, 0.4759473930370735), std::complex<T>(-0.9458172417006346, 0.32469946920468346), std::complex<T>(-0.9863613034027223, 0.1645945902807339),
		        std::complex<T>(-1.0, -0.0), std::complex<T>(-0.9863613034027223, -0.1645945902807339), std::complex<T>(-0.9458172417006346, -0.32469946920468346),
		        std::complex<T>(-0.8794737512064891, -0.4759473930370735), std::complex<T>(-0.7891405093963936, -0.6142127126896678), std::complex<T>(-0.6772815716257411, -0.7357239106731316),
		        std::complex<T>(-0.5469481581224269, -0.8371664782625285), std::complex<T>(-0.40169542465296953, -0.9157733266550574), std::complex<T>(-0.24548548714079924, -0.9694002659393304),
		        std::complex<T>(-0.0825793454723324, -0.9965844930066698)},
		    1.0};
	case 20:
		return {{},
		    {std::complex<T>(-0.078459095727845, 0.996917333733128), std::complex<T>(-0.23344536385590525, 0.9723699203976767), std::complex<T>(-0.38268343236508984, 0.9238795325112867),
		        std::complex<T>(-0.5224985647159487, 0.8526401643540923), std::complex<T>(-0.6494480483301837, 0.7604059656000309), std::complex<T>(-0.7604059656000309, 0.6494480483301837),
		        std::complex<T>(-0.8526401643540923, 0.5224985647159488), std::complex<T>(-0.9238795325112867, 0.3826834323650898), std::complex<T>(-0.9723699203976766, 0.2334453638559054),
		        std::complex<T>(-0.996917333733128, 0.07845909572784494), std::complex<T>(-0.996917333733128, -0.07845909572784494), std::complex<T>(-0.9723699203976766, -0.2334453638559054),
		        std::complex<T>(-0.9238795325112867, -0.3826834323650898), std::complex<T>(-0.8526401643540923, -0.5224985647159488), std::complex<T>(-0.7604059656000309, -0.6494480483301837),
		        std::complex<T>(-0.6494480483301837, -0.7604059656000309), std::complex<T>(-0.5224985647159487, -0.8526401643540923), std::complex<T>(-0.38268343236508984, -0.9238795325112867),
		        std::complex<T>(-0.23344536385590525, -0.9723699203976767), std::complex<T>(-0.078459095727845, -0.996917333733128)},
		    1.0};
	case 21:
		return {{},
		    {std::complex<T>(-0.07473009358642439, 0.9972037971811801), std::complex<T>(-0.22252093395631445, 0.9749279121818236), std::complex<T>(-0.3653410243663952, 0.9308737486442041),
		        std::complex<T>(-0.5000000000000001, 0.8660254037844386), std::complex<T>(-0.6234898018587336, 0.7818314824680298), std::complex<T>(-0.7330518718298263, 0.6801727377709194),
		        std::complex<T>(-0.8262387743159949, 0.563320058063622), std::complex<T>(-0.9009688679024191, 0.4338837391175581), std::complex<T>(-0.9555728057861408, 0.29475517441090415),
		        std::complex<T>(-0.9888308262251285, 0.14904226617617441), std::complex<T>(-1.0, -0.0), std::complex<T>(-0.9888308262251285, -0.14904226617617441),
		        std::complex<T>(-0.9555728057861408, -0.29475517441090415), std::complex<T>(-0.9009688679024191, -0.4338837391175581), std::complex<T>(-0.8262387743159949, -0.563320058063622),
		        std::complex<T>(-0.7330518718298263, -0.6801727377709194), std::complex<T>(-0.6234898018587336, -0.7818314824680298), std::complex<T>(-0.5000000000000001, -0.8660254037844386),
		        std::complex<T>(-0.3653410243663952, -0.9308737486442041), std::complex<T>(-0.22252093395631445, -0.9749279121818236), std::complex<T>(-0.07473009358642439, -0.9972037971811801)},
		    1.0};
	case 22:
		return {{},
		    {std::complex<T>(-0.07133918319923235, 0.9974521146102535), std::complex<T>(-0.21256528955297682, 0.9771468659711595), std::complex<T>(-0.3494641795990982, 0.9369497249997618),
		        std::complex<T>(-0.479248986720057, 0.8776789895672555), std::complex<T>(-0.5992776665113468, 0.8005412409243605), std::complex<T>(-0.7071067811865477, 0.7071067811865475),
		        std::complex<T>(-0.8005412409243604, 0.5992776665113468), std::complex<T>(-0.8776789895672557, 0.4792489867200568), std::complex<T>(-0.9369497249997617, 0.34946417959909837),
		        std::complex<T>(-0.9771468659711595, 0.21256528955297668), std::complex<T>(-0.9974521146102535, 0.07133918319923234), std::complex<T>(-0.9974521146102535, -0.07133918319923234),
		        std::complex<T>(-0.9771468659711595, -0.21256528955297668), std::complex<T>(-0.9369497249997617, -0.34946417959909837), std::complex<T>(-0.8776789895672557, -0.4792489867200568),
		        std::complex<T>(-0.8005412409243604, -0.5992776665113468), std::complex<T>(-0.7071067811865477, -0.7071067811865475), std::complex<T>(-0.5992776665113468, -0.8005412409243605),
		        std::complex<T>(-0.479248986720057, -0.8776789895672555), std::complex<T>(-0.3494641795990982, -0.9369497249997618), std::complex<T>(-0.21256528955297682, -0.9771468659711595),
		        std::complex<T>(-0.07133918319923235, -0.9974521146102535)},
		    1.0};
	case 23:
		return {{},
		    {std::complex<T>(-0.06824241336467123, 0.9976687691905392), std::complex<T>(-0.20345601305263397, 0.9790840876823228), std::complex<T>(-0.3348796121709863, 0.9422609221188204),
		        std::complex<T>(-0.4600650377311522, 0.8878852184023752), std::complex<T>(-0.5766803221148672, 0.816969893010442), std::complex<T>(-0.6825531432186541, 0.730835964278124),
		        std::complex<T>(-0.7757112907044199, 0.6310879443260528), std::complex<T>(-0.8544194045464886, 0.5195839500354336), std::complex<T>(-0.917211301505453, 0.39840108984624145),
		        std::complex<T>(-0.9629172873477994, 0.2697967711570243), std::complex<T>(-0.9906859460363308, 0.1361666490962466), std::complex<T>(-1.0, -0.0),
		        std::complex<T>(-0.9906859460363308, -0.1361666490962466), std::complex<T>(-0.9629172873477994, -0.2697967711570243), std::complex<T>(-0.917211301505453, -0.39840108984624145),
		        std::complex<T>(-0.8544194045464886, -0.5195839500354336), std::complex<T>(-0.7757112907044199, -0.6310879443260528), std::complex<T>(-0.6825531432186541, -0.730835964278124),
		        std::complex<T>(-0.5766803221148672, -0.816969893010442), std::complex<T>(-0.4600650377311522, -0.8878852184023752), std::complex<T>(-0.3348796121709863, -0.9422609221188204),
		        std::complex<T>(-0.20345601305263397, -0.9790840876823228), std::complex<T>(-0.06824241336467123, -0.9976687691905392)},
		    1.0};
	case 24:
		return {{},
		    {std::complex<T>(-0.06540312923014327, 0.9978589232386035), std::complex<T>(-0.19509032201612833, 0.9807852804032304), std::complex<T>(-0.3214394653031617, 0.9469301294951056),
		        std::complex<T>(-0.44228869021900125, 0.8968727415326884), std::complex<T>(-0.5555702330196024, 0.8314696123025451), std::complex<T>(-0.6593458151000688, 0.7518398074789774),
		        std::complex<T>(-0.7518398074789775, 0.6593458151000687), std::complex<T>(-0.8314696123025452, 0.5555702330196022), std::complex<T>(-0.8968727415326884, 0.44228869021900125),
		        std::complex<T>(-0.9469301294951057, 0.32143946530316153), std::complex<T>(-0.9807852804032304, 0.19509032201612825), std::complex<T>(-0.9978589232386035, 0.06540312923014306),
		        std::complex<T>(-0.9978589232386035, -0.06540312923014306), std::complex<T>(-0.9807852804032304, -0.19509032201612825), std::complex<T>(-0.9469301294951057, -0.32143946530316153),
		        std::complex<T>(-0.8968727415326884, -0.44228869021900125), std::complex<T>(-0.8314696123025452, -0.5555702330196022), std::complex<T>(-0.7518398074789775, -0.6593458151000687),
		        std::complex<T>(-0.6593458151000688, -0.7518398074789774), std::complex<T>(-0.5555702330196024, -0.8314696123025451), std::complex<T>(-0.44228869021900125, -0.8968727415326884),
		        std::complex<T>(-0.3214394653031617, -0.9469301294951056), std::complex<T>(-0.19509032201612833, -0.9807852804032304), std::complex<T>(-0.06540312923014327, -0.9978589232386035)},
		    1.0};
	case 25:
		return {{},
		    {std::complex<T>(-0.0627905195293133, 0.9980267284282716), std::complex<T>(-0.18738131458572474, 0.9822872507286886), std::complex<T>(-0.30901699437494745, 0.9510565162951535),
		        std::complex<T>(-0.42577929156507266, 0.9048270524660196), std::complex<T>(-0.5358267949789965, 0.8443279255020151), std::complex<T>(-0.6374239897486897, 0.7705132427757893),
		        std::complex<T>(-0.7289686274214116, 0.6845471059286887), std::complex<T>(-0.8090169943749475, 0.5877852522924731), std::complex<T>(-0.8763066800438636, 0.4817536741017153),
		        std::complex<T>(-0.9297764858882513, 0.368124552684678), std::complex<T>(-0.9685831611286311, 0.2486898871648548), std::complex<T>(-0.9921147013144779, 0.12533323356430426),
		        std::complex<T>(-1.0, -0.0), std::complex<T>(-0.9921147013144779, -0.12533323356430426), std::complex<T>(-0.9685831611286311, -0.2486898871648548),
		        std::complex<T>(-0.9297764858882513, -0.368124552684678), std::complex<T>(-0.8763066800438636, -0.4817536741017153), std::complex<T>(-0.8090169943749475, -0.5877852522924731),
		        std::complex<T>(-0.7289686274214116, -0.6845471059286887), std::complex<T>(-0.6374239897486897, -0.7705132427757893), std::complex<T>(-0.5358267949789965, -0.8443279255020151),
		        std::complex<T>(-0.42577929156507266, -0.9048270524660196), std::complex<T>(-0.30901699437494745, -0.9510565162951535), std::complex<T>(-0.18738131458572474, -0.9822872507286886),
		        std::complex<T>(-0.0627905195293133, -0.9980267284282716)},
		    1.0};
	case 26:
		return {{},
		    {std::complex<T>(-0.060378497422286063, 0.9981755542233175), std::complex<T>(-0.18025503781390587, 0.9836199069471435), std::complex<T>(-0.297503053855203, 0.9547208665085456),
		        std::complex<T>(-0.41041280545275693, 0.9118998459920901), std::complex<T>(-0.5173378141776568, 0.8557812723014475), std::complex<T>(-0.6167188726285431, 0.7871834806090502),
		        std::complex<T>(-0.7071067811865475, 0.7071067811865476), std::complex<T>(-0.7871834806090502, 0.6167188726285431), std::complex<T>(-0.8557812723014475, 0.5173378141776568),
		        std::complex<T>(-0.9118998459920901, 0.4104128054527568), std::complex<T>(-0.9547208665085456, 0.297503053855203), std::complex<T>(-0.9836199069471436, 0.18025503781390576),
		        std::complex<T>(-0.9981755542233175, 0.06037849742228606), std::complex<T>(-0.9981755542233175, -0.06037849742228606), std::complex<T>(-0.9836199069471436, -0.18025503781390576),
		        std::complex<T>(-0.9547208665085456, -0.297503053855203), std::complex<T>(-0.9118998459920901, -0.4104128054527568), std::complex<T>(-0.8557812723014475, -0.5173378141776568),
		        std::complex<T>(-0.7871834806090502, -0.6167188726285431), std::complex<T>(-0.7071067811865475, -0.7071067811865476), std::complex<T>(-0.6167188726285431, -0.7871834806090502),
		        std::complex<T>(-0.5173378141776568, -0.8557812723014475), std::complex<T>(-0.41041280545275693, -0.9118998459920901), std::complex<T>(-0.297503053855203, -0.9547208665085456),
		        std::complex<T>(-0.18025503781390587, -0.9836199069471435), std::complex<T>(-0.060378497422286063, -0.9981755542233175)},
		    1.0};
	case 27:
		return {{},
		    {std::complex<T>(-0.0581448289104759, 0.9983081582712682), std::complex<T>(-0.17364817766693041, 0.984807753012208), std::complex<T>(-0.28680323271109054, 0.9579895123154888),
		        std::complex<T>(-0.3960797660391569, 0.918216106880274), std::complex<T>(-0.5000000000000001, 0.8660254037844386), std::complex<T>(-0.5971585917027863, 0.8021231927550437),
		        std::complex<T>(-0.6862416378687336, 0.7273736415730487), std::complex<T>(-0.766044443118978, 0.6427876096865393), std::complex<T>(-0.8354878114129365, 0.549508978070806),
		        std::complex<T>(-0.8936326403234123, 0.4487991802004621), std::complex<T>(-0.9396926207859084, 0.3420201433256687), std::complex<T>(-0.9730448705798238, 0.23061587074244014),
		        std::complex<T>(-0.993238357741943, 0.11609291412523022), std::complex<T>(-1.0, -0.0), std::complex<T>(-0.993238357741943, -0.11609291412523022),
		        std::complex<T>(-0.9730448705798238, -0.23061587074244014), std::complex<T>(-0.9396926207859084, -0.3420201433256687), std::complex<T>(-0.8936326403234123, -0.4487991802004621),
		        std::complex<T>(-0.8354878114129365, -0.549508978070806), std::complex<T>(-0.766044443118978, -0.6427876096865393), std::complex<T>(-0.6862416378687336, -0.7273736415730487),
		        std::complex<T>(-0.5971585917027863, -0.8021231927550437), std::complex<T>(-0.5000000000000001, -0.8660254037844386), std::complex<T>(-0.3960797660391569, -0.918216106880274),
		        std::complex<T>(-0.28680323271109054, -0.9579895123154888), std::complex<T>(-0.17364817766693041, -0.984807753012208), std::complex<T>(-0.0581448289104759, -0.9983081582712682)},
		    1.0};
	case 28:
		return {{},
		    {std::complex<T>(-0.05607044723719207, 0.9984268150178165), std::complex<T>(-0.16750622330473647, 0.9858710185182359), std::complex<T>(-0.27683551142484963, 0.9609173219450995),
		        std::complex<T>(-0.38268343236508984, 0.9238795325112867), std::complex<T>(-0.48371888710524, 0.8752234219087536), std::complex<T>(-0.5786712961798057, 0.8155608689592602),
		        std::complex<T>(-0.666346577952004, 0.7456421648831655), std::complex<T>(-0.7456421648831656, 0.6663465779520039), std::complex<T>(-0.8155608689592603, 0.5786712961798056),
		        std::complex<T>(-0.8752234219087537, 0.48371888710523975), std::complex<T>(-0.9238795325112867, 0.3826834323650898), std::complex<T>(-0.9609173219450996, 0.27683551142484936),
		        std::complex<T>(-0.9858710185182359, 0.1675062233047364), std::complex<T>(-0.9984268150178166, 0.05607044723719179), std::complex<T>(-0.9984268150178166, -0.05607044723719179),
		        std::complex<T>(-0.9858710185182359, -0.1675062233047364), std::complex<T>(-0.9609173219450996, -0.27683551142484936), std::complex<T>(-0.9238795325112867, -0.3826834323650898),
		        std::complex<T>(-0.8752234219087537, -0.48371888710523975), std::complex<T>(-0.8155608689592603, -0.5786712961798056), std::complex<T>(-0.7456421648831656, -0.6663465779520039),
		        std::complex<T>(-0.666346577952004, -0.7456421648831655), std::complex<T>(-0.5786712961798057, -0.8155608689592602), std::complex<T>(-0.48371888710524, -0.8752234219087536),
		        std::complex<T>(-0.38268343236508984, -0.9238795325112867), std::complex<T>(-0.27683551142484963, -0.9609173219450995), std::complex<T>(-0.16750622330473647, -0.9858710185182359),
		        std::complex<T>(-0.05607044723719207, -0.9984268150178165)},
		    1.0};
	case 29:
		return {{},
		    {std::complex<T>(-0.05413890858541761, 0.9985334138511238), std::complex<T>(-0.16178199655276462, 0.9868265225415261), std::complex<T>(-0.267528338529221, 0.9635499925192229),
		        std::complex<T>(-0.37013815533991457, 0.9289767198167913), std::complex<T>(-0.46840844069979015, 0.8835120444460229), std::complex<T>(-0.5611870653623824, 0.8276889981568906),
		        std::complex<T>(-0.6473862847818277, 0.7621620551276365), std::complex<T>(-0.7259954919231308, 0.6876994588534232), std::complex<T>(-0.7960930657056438, 0.6051742151937651),
		        std::complex<T>(-0.8568571761675893, 0.5155538571770217), std::complex<T>(-0.907575419670957, 0.4198891015602646), std::complex<T>(-0.9476531711828025, 0.31930153013597995),
		        std::complex<T>(-0.9766205557100867, 0.21497044021102407), std::complex<T>(-0.9941379571543596, 0.10811901842394177), std::complex<T>(-1.0, -0.0),
		        std::complex<T>(-0.9941379571543596, -0.10811901842394177), std::complex<T>(-0.9766205557100867, -0.21497044021102407), std::complex<T>(-0.9476531711828025, -0.31930153013597995),
		        std::complex<T>(-0.907575419670957, -0.4198891015602646), std::complex<T>(-0.8568571761675893, -0.5155538571770217), std::complex<T>(-0.7960930657056438, -0.6051742151937651),
		        std::complex<T>(-0.7259954919231308, -0.6876994588534232), std::complex<T>(-0.6473862847818277, -0.7621620551276365), std::complex<T>(-0.5611870653623824, -0.8276889981568906),
		        std::complex<T>(-0.46840844069979015, -0.8835120444460229), std::complex<T>(-0.37013815533991457, -0.9289767198167913), std::complex<T>(-0.267528338529221, -0.9635499925192229),
		        std::complex<T>(-0.16178199655276462, -0.9868265225415261), std::complex<T>(-0.05413890858541761, -0.9985334138511238)},
		    1.0};
	case 30:
		return {{},
		    {std::complex<T>(-0.052335956242943744, 0.9986295347545738), std::complex<T>(-0.15643446504023092, 0.9876883405951378), std::complex<T>(-0.25881904510252074, 0.9659258262890683),
		        std::complex<T>(-0.3583679495453004, 0.9335804264972017), std::complex<T>(-0.4539904997395468, 0.8910065241883678), std::complex<T>(-0.5446390350150272, 0.8386705679454239),
		        std::complex<T>(-0.6293203910498375, 0.7771459614569709), std::complex<T>(-0.7071067811865477, 0.7071067811865475), std::complex<T>(-0.7771459614569708, 0.6293203910498375),
		        std::complex<T>(-0.838670567945424, 0.544639035015027), std::complex<T>(-0.8910065241883679, 0.45399049973954675), std::complex<T>(-0.9335804264972017, 0.35836794954530027),
		        std::complex<T>(-0.9659258262890683, 0.25881904510252074), std::complex<T>(-0.9876883405951378, 0.15643446504023087), std::complex<T>(-0.9986295347545738, 0.05233595624294383),
		        std::complex<T>(-0.9986295347545738, -0.05233595624294383), std::complex<T>(-0.9876883405951378, -0.15643446504023087), std::complex<T>(-0.9659258262890683, -0.25881904510252074),
		        std::complex<T>(-0.9335804264972017, -0.35836794954530027), std::complex<T>(-0.8910065241883679, -0.45399049973954675), std::complex<T>(-0.838670567945424, -0.544639035015027),
		        std::complex<T>(-0.7771459614569708, -0.6293203910498375), std::complex<T>(-0.7071067811865477, -0.7071067811865475), std::complex<T>(-0.6293203910498375, -0.7771459614569709),
		        std::complex<T>(-0.5446390350150272, -0.8386705679454239), std::complex<T>(-0.4539904997395468, -0.8910065241883678), std::complex<T>(-0.3583679495453004, -0.9335804264972017),
		        std::complex<T>(-0.25881904510252074, -0.9659258262890683), std::complex<T>(-0.15643446504023092, -0.9876883405951378), std::complex<T>(-0.052335956242943744, -0.9986295347545738)},
		    1.0};
	default: return {{}, {}, 1.0};
	}

	std::vector<std::complex<T>> z;

	// m = np.arange(-N+1, N, 2)
	// 也就是从 -N+1 到 N-1 (含)，步进 2
	std::vector<int> m;
	m.reserve((N + (N - 1)) / 2); // 简单预估一下容量
	for (int val = -N + 1; val < N; val += 2) { m.push_back(val); }

	// p = -exp(1j * pi * m / (2*N))
	// 需要对 m 中的每个值依次计算
	std::vector<std::complex<T>> p;
	p.reserve(m.size());
	for (auto val : m) {
		// 计算 exponent 的角度
		T angle = M_PI * (static_cast<T>(val) / (2 * static_cast<T>(N)));
		// 复数形式: e^(j * angle)
		std::complex<T> exp_j_angle = std::exp(std::complex<T>(0, 1) * angle);
		// p_i = - e^(j * angle)
		p.push_back(-exp_j_angle);
	}

	// k = 1
	T k = static_cast<T>(1);
	return {z, p, k};
}

template <typename T>
zpk<T> bessel(int N) {
	switch (N) {
	case 0: return {{}, {}, 1.0};
	case 1: return {{}, {std::complex<T>(-1., +0.)}, 1.0};
	case 2: return {{}, {std::complex<T>(-0.8660254037844385, -0.4999999999999999), std::complex<T>(-0.8660254037844385, +0.4999999999999999)}, 1.0};
	case 3: return {{}, {std::complex<T>(-0.7456403858480766, -0.7113666249728353), std::complex<T>(-0.9416000265332067, +0.), std::complex<T>(-0.7456403858480766, +0.7113666249728353)}, 1.0};
	case 4:
		return {{},
		    {std::complex<T>(-0.6572111716718827, -0.830161435004873), std::complex<T>(-0.904758796788245, -0.27091873300387465), std::complex<T>(-0.904758796788245, +0.27091873300387465),
		        std::complex<T>(-0.6572111716718827, +0.830161435004873)},
		    1.0};
	case 5:
		return {{},
		    {std::complex<T>(-0.5905759446119192, -0.9072067564574549), std::complex<T>(-0.8515536193688396, -0.44271746394433276), std::complex<T>(-0.92644207738776, +0.),
		        std::complex<T>(-0.8515536193688396, +0.44271746394433276), std::complex<T>(-0.5905759446119192, +0.9072067564574549)},
		    1.0};
	case 6:
		return {{},
		    {std::complex<T>(-0.5385526816693108, -0.9616876881954276), std::complex<T>(-0.7996541858328287, -0.5621717346937316), std::complex<T>(-0.9093906830472273, -0.1856964396793047),
		        std::complex<T>(-0.9093906830472273, +0.1856964396793047), std::complex<T>(-0.7996541858328287, +0.5621717346937316), std::complex<T>(-0.5385526816693108, +0.9616876881954276)},
		    1.0};
	case 7:
		return {{},
		    {std::complex<T>(-0.4966917256672317, -1.0025085084544205), std::complex<T>(-0.7527355434093214, -0.6504696305522553), std::complex<T>(-0.8800029341523379, -0.32166527623077407),
		        std::complex<T>(-0.919487155649029, +0.), std::complex<T>(-0.8800029341523379, +0.32166527623077407), std::complex<T>(-0.7527355434093214, +0.6504696305522553),
		        std::complex<T>(-0.4966917256672317, +1.0025085084544205)},
		    1.0};
	case 8:
		return {{},
		    {std::complex<T>(-0.46217404125321254, -1.0343886811269012), std::complex<T>(-0.7111381808485397, -0.71865173141084), std::complex<T>(-0.8473250802359334, -0.42590175382729345),
		        std::complex<T>(-0.9096831546652911, -0.1412437976671423), std::complex<T>(-0.9096831546652911, +0.1412437976671423), std::complex<T>(-0.8473250802359334, +0.42590175382729345),
		        std::complex<T>(-0.7111381808485397, +0.71865173141084), std::complex<T>(-0.46217404125321254, +1.0343886811269012)},
		    1.0};
	case 9:
		return {{},
		    {std::complex<T>(-0.43314155615536226, -1.0600736701359301), std::complex<T>(-0.6743622686854763, -0.7730546212691185), std::complex<T>(-0.8148021112269014, -0.50858156896315),
		        std::complex<T>(-0.8911217017079759, -0.25265809345821644), std::complex<T>(-0.9154957797499037, +0.), std::complex<T>(-0.8911217017079759, +0.25265809345821644),
		        std::complex<T>(-0.8148021112269014, +0.50858156896315), std::complex<T>(-0.6743622686854763, +0.7730546212691185), std::complex<T>(-0.43314155615536226, +1.0600736701359301)},
		    1.0};
	case 10:
		return {{},
		    {std::complex<T>(-0.4083220732868863, -1.0812748428191246), std::complex<T>(-0.6417513866988322, -0.8175836167191022), std::complex<T>(-0.7837694413101445, -0.5759147538499948),
		        std::complex<T>(-0.8688459641284763, -0.34300082337663096), std::complex<T>(-0.9091347320900505, -0.11395831373355114), std::complex<T>(-0.9091347320900505, +0.11395831373355114),
		        std::complex<T>(-0.8688459641284763, +0.34300082337663096), std::complex<T>(-0.7837694413101445, +0.5759147538499948), std::complex<T>(-0.6417513866988322, +0.8175836167191022),
		        std::complex<T>(-0.4083220732868863, +1.0812748428191246)},
		    1.0};
	case 11:
		return {{},
		    {std::complex<T>(-0.3868149510055096, -1.0991174667631216), std::complex<T>(-0.6126871554915198, -0.8547813893314768), std::complex<T>(-0.7546938934722305, -0.6319150050721849),
		        std::complex<T>(-0.8453044014712964, -0.4178696917801249), std::complex<T>(-0.8963656705721169, -0.20804803750710327), std::complex<T>(-0.9129067244518985, +0.),
		        std::complex<T>(-0.8963656705721169, +0.20804803750710327), std::complex<T>(-0.8453044014712964, +0.4178696917801249), std::complex<T>(-0.7546938934722305, +0.6319150050721849),
		        std::complex<T>(-0.6126871554915198, +0.8547813893314768), std::complex<T>(-0.3868149510055096, +1.0991174667631216)},
		    1.0};
	case 12:
		return {{},
		    {std::complex<T>(-0.3679640085526314, -1.1143735756415463), std::complex<T>(-0.5866369321861475, -0.8863772751320727), std::complex<T>(-0.7276681615395161, -0.6792961178764695),
		        std::complex<T>(-0.8217296939939074, -0.48102121151006755), std::complex<T>(-0.880253434201683, -0.2871779503524227), std::complex<T>(-0.9084478234140686, -0.09550636521345042),
		        std::complex<T>(-0.9084478234140686, +0.09550636521345042), std::complex<T>(-0.880253434201683, +0.2871779503524227), std::complex<T>(-0.8217296939939074, +0.48102121151006755),
		        std::complex<T>(-0.7276681615395161, +0.6792961178764695), std::complex<T>(-0.5866369321861475, +0.8863772751320727), std::complex<T>(-0.3679640085526314, +1.1143735756415463)},
		    1.0};
	case 13:
		return {{},
		    {std::complex<T>(-0.35127923233898156, -1.1275915483177048), std::complex<T>(-0.5631559842430193, -0.9135900338325103), std::complex<T>(-0.7026234675721276, -0.7199611890171304),
		        std::complex<T>(-0.7987460692470972, -0.5350752120696802), std::complex<T>(-0.8625094198260553, -0.35474137311729914), std::complex<T>(-0.8991314665475196, -0.17683429561610436),
		        std::complex<T>(-0.9110914665984182, +0.), std::complex<T>(-0.8991314665475196, +0.17683429561610436), std::complex<T>(-0.8625094198260553, +0.35474137311729914),
		        std::complex<T>(-0.7987460692470972, +0.5350752120696802), std::complex<T>(-0.7026234675721276, +0.7199611890171304), std::complex<T>(-0.5631559842430193, +0.9135900338325103),
		        std::complex<T>(-0.35127923233898156, +1.1275915483177048)},
		    1.0};
	case 14:
		return {{},
		    {std::complex<T>(-0.3363868224902031, -1.1391722978398593), std::complex<T>(-0.5418766775112306, -0.9373043683516926), std::complex<T>(-0.6794256425119227, -0.7552857305042031),
		        std::complex<T>(-0.7766591387063624, -0.581917067737761), std::complex<T>(-0.8441199160909851, -0.41316538251026935), std::complex<T>(-0.8869506674916446, -0.24700791787653334),
		        std::complex<T>(-0.9077932138396491, -0.08219639941940154), std::complex<T>(-0.9077932138396491, +0.08219639941940154), std::complex<T>(-0.8869506674916446, +0.24700791787653334),
		        std::complex<T>(-0.8441199160909851, +0.41316538251026935), std::complex<T>(-0.7766591387063624, +0.581917067737761), std::complex<T>(-0.6794256425119227, +0.7552857305042031),
		        std::complex<T>(-0.5418766775112306, +0.9373043683516926), std::complex<T>(-0.3363868224902031, +1.1391722978398593)},
		    1.0};
	case 15:
		return {{},
		    {std::complex<T>(-0.3229963059766445, -1.14941615458363), std::complex<T>(-0.5224954069658334, -0.9581787261092526), std::complex<T>(-0.6579196593111002, -0.7862895503722519),
		        std::complex<T>(-0.7556027168970723, -0.6229396358758262), std::complex<T>(-0.8256631452587148, -0.46423487527343266), std::complex<T>(-0.8731264620834984, -0.30823524705642674),
		        std::complex<T>(-0.9006981694176978, -0.1537681197278439), std::complex<T>(-0.9097482363849062, +0.), std::complex<T>(-0.9006981694176978, +0.1537681197278439),
		        std::complex<T>(-0.8731264620834984, +0.30823524705642674), std::complex<T>(-0.8256631452587148, +0.46423487527343266), std::complex<T>(-0.7556027168970723, +0.6229396358758262),
		        std::complex<T>(-0.6579196593111002, +0.7862895503722519), std::complex<T>(-0.5224954069658334, +0.9581787261092526), std::complex<T>(-0.3229963059766445, +1.14941615458363)},
		    1.0};
	case 16:
		return {{},
		    {std::complex<T>(-0.31087827556453856, -1.1585528411993304), std::complex<T>(-0.504760644442476, -0.9767137477799086), std::complex<T>(-0.6379502514039066, -0.8137453537108762),
		        std::complex<T>(-0.7356166304713119, -0.6591950877860395), std::complex<T>(-0.8074790293236005, -0.5092933751171799), std::complex<T>(-0.8584264231521322, -0.3621697271802063),
		        std::complex<T>(-0.8911723070323642, -0.2167089659900575), std::complex<T>(-0.9072099595087003, -0.07214211304111734), std::complex<T>(-0.9072099595087003, +0.07214211304111734),
		        std::complex<T>(-0.8911723070323642, +0.2167089659900575), std::complex<T>(-0.8584264231521322, +0.3621697271802063), std::complex<T>(-0.8074790293236005, +0.5092933751171799),
		        std::complex<T>(-0.7356166304713119, +0.6591950877860395), std::complex<T>(-0.6379502514039066, +0.8137453537108762), std::complex<T>(-0.504760644442476, +0.9767137477799086),
		        std::complex<T>(-0.31087827556453856, +1.1585528411993304)},
		    1.0};
	case 17:
		return {{},
		    {std::complex<T>(-0.29984894599900724, -1.1667612729256673), std::complex<T>(-0.48846293376727057, -0.9932971956316782), std::complex<T>(-0.6193710717342136, -0.8382497252826987),
		        std::complex<T>(-0.7166893842372348, -0.6914936286393606), std::complex<T>(-0.7897644147799701, -0.5493724405281085), std::complex<T>(-0.8433414495836128, -0.41007592829100215),
		        std::complex<T>(-0.8801100704438625, -0.2725347156478803), std::complex<T>(-0.9016273850787279, -0.13602679951730237), std::complex<T>(-0.9087141161336388, +0.),
		        std::complex<T>(-0.9016273850787279, +0.13602679951730237), std::complex<T>(-0.8801100704438625, +0.2725347156478803), std::complex<T>(-0.8433414495836128, +0.41007592829100215),
		        std::complex<T>(-0.7897644147799701, +0.5493724405281085), std::complex<T>(-0.7166893842372348, +0.6914936286393606), std::complex<T>(-0.6193710717342136, +0.8382497252826987),
		        std::complex<T>(-0.48846293376727057, +0.9932971956316782), std::complex<T>(-0.29984894599900724, +1.1667612729256673)},
		    1.0};
	case 18:
		return {{},
		    {std::complex<T>(-0.28975920298804847, -1.1741830106000584), std::complex<T>(-0.4734268069916154, -1.0082343003148009), std::complex<T>(-0.6020482668090646, -0.8602708961893666),
		        std::complex<T>(-0.698782144500527, -0.7204696509726628), std::complex<T>(-0.7726285030739557, -0.5852778162086639), std::complex<T>(-0.8281885016242831, -0.45293856978159136),
		        std::complex<T>(-0.8681095503628832, -0.32242049251632576), std::complex<T>(-0.8939764278132456, -0.19303746408947586), std::complex<T>(-0.9067004324162776, -0.06427924106393067),
		        std::complex<T>(-0.9067004324162776, +0.06427924106393067), std::complex<T>(-0.8939764278132456, +0.19303746408947586), std::complex<T>(-0.8681095503628832, +0.32242049251632576),
		        std::complex<T>(-0.8281885016242831, +0.45293856978159136), std::complex<T>(-0.7726285030739557, +0.5852778162086639), std::complex<T>(-0.698782144500527, +0.7204696509726628),
		        std::complex<T>(-0.6020482668090646, +0.8602708961893666), std::complex<T>(-0.4734268069916154, +1.0082343003148009), std::complex<T>(-0.28975920298804847, +1.1741830106000584)},
		    1.0};
	case 19:
		return {{},
		    {std::complex<T>(-0.2804866851439361, -1.1809316284532905), std::complex<T>(-0.4595043449730983, -1.0217687769126707), std::complex<T>(-0.5858613321217832, -0.8801817131014564),
		        std::complex<T>(-0.6818424412912442, -0.7466272357947761), std::complex<T>(-0.7561260971541627, -0.6176483917970176), std::complex<T>(-0.8131725551578203, -0.491536503556246),
		        std::complex<T>(-0.8555768765618422, -0.3672925896399872), std::complex<T>(-0.8849290585034385, -0.24425907575498182), std::complex<T>(-0.9021937639390656, -0.12195683818720263),
		        std::complex<T>(-0.9078934217899399, +0.), std::complex<T>(-0.9021937639390656, +0.12195683818720263), std::complex<T>(-0.8849290585034385, +0.24425907575498182),
		        std::complex<T>(-0.8555768765618422, +0.3672925896399872), std::complex<T>(-0.8131725551578203, +0.491536503556246), std::complex<T>(-0.7561260971541627, +0.6176483917970176),
		        std::complex<T>(-0.6818424412912442, +0.7466272357947761), std::complex<T>(-0.5858613321217832, +0.8801817131014564), std::complex<T>(-0.4595043449730983, +1.0217687769126707),
		        std::complex<T>(-0.2804866851439361, +1.1809316284532905)},
		    1.0};
	case 20:
		return {{},
		    {std::complex<T>(-0.27192995802516506, -1.187099379810886), std::complex<T>(-0.44657006982051484, -1.0340977025608422), std::complex<T>(-0.5707026806915716, -0.8982829066468254),
		        std::complex<T>(-0.6658120544829932, -0.7703721701100759), std::complex<T>(-0.7402780309646764, -0.6469975237605227), std::complex<T>(-0.7984251191290602, -0.526494238881713),
		        std::complex<T>(-0.8427907479956664, -0.4078917326291931), std::complex<T>(-0.8749560316673335, -0.2905559296567909), std::complex<T>(-0.8959150941925766, -0.17403171759187044),
		        std::complex<T>(-0.9062570115576768, -0.0579617802778495), std::complex<T>(-0.9062570115576768, +0.0579617802778495), std::complex<T>(-0.8959150941925766, +0.17403171759187044),
		        std::complex<T>(-0.8749560316673335, +0.2905559296567909), std::complex<T>(-0.8427907479956664, +0.4078917326291931), std::complex<T>(-0.7984251191290602, +0.526494238881713),
		        std::complex<T>(-0.7402780309646764, +0.6469975237605227), std::complex<T>(-0.6658120544829932, +0.7703721701100759), std::complex<T>(-0.5707026806915716, +0.8982829066468254),
		        std::complex<T>(-0.44657006982051484, +1.0340977025608422), std::complex<T>(-0.27192995802516506, +1.187099379810886)},
		    1.0};
	case 21:
		return {{},
		    {std::complex<T>(-0.2640041595834027, -1.192762031948052), std::complex<T>(-0.4345168906815268, -1.045382255856986), std::complex<T>(-0.5564766488918566, -0.9148198405846728),
		        std::complex<T>(-0.6506315378609466, -0.7920349342629495), std::complex<T>(-0.7250839687106612, -0.6737426063024383), std::complex<T>(-0.7840287980408347, -0.5583186348022857),
		        std::complex<T>(-0.8299435470674444, -0.44481777394079575), std::complex<T>(-0.8643915813643203, -0.33262585125221866), std::complex<T>(-0.8883808106664449, -0.221306921508435),
		        std::complex<T>(-0.9025428073192694, -0.11052525727898564), std::complex<T>(-0.9072262653142963, +0.), std::complex<T>(-0.9025428073192694, +0.11052525727898564),
		        std::complex<T>(-0.8883808106664449, +0.221306921508435), std::complex<T>(-0.8643915813643203, +0.33262585125221866), std::complex<T>(-0.8299435470674444, +0.44481777394079575),
		        std::complex<T>(-0.7840287980408347, +0.5583186348022857), std::complex<T>(-0.7250839687106612, +0.6737426063024383), std::complex<T>(-0.6506315378609466, +0.7920349342629495),
		        std::complex<T>(-0.5564766488918566, +0.9148198405846728), std::complex<T>(-0.4345168906815268, +1.045382255856986), std::complex<T>(-0.2640041595834027, +1.192762031948052)},
		    1.0};

	case 22:
		return {{},
		    {std::complex<T>(-0.2566376987939318, -1.1979824335552132), std::complex<T>(-0.4232528745642629, -1.055755605227546), std::complex<T>(-0.5430983056306306, -0.9299947824439877),
		        std::complex<T>(-0.6362427683267828, -0.811887504024635), std::complex<T>(-0.7105305456418792, -0.6982266265924527), std::complex<T>(-0.7700332930556816, -0.5874255426351151),
		        std::complex<T>(-0.8171682088462721, -0.47856194922027806), std::complex<T>(-0.8534754036851689, -0.37103893194823206), std::complex<T>(-0.8799661455640174, -0.26443630392015344),
		        std::complex<T>(-0.8972983138153532, -0.15843519122898653), std::complex<T>(-0.9058702269930871, -0.05277490828999903), std::complex<T>(-0.9058702269930871, +0.05277490828999903),
		        std::complex<T>(-0.8972983138153532, +0.15843519122898653), std::complex<T>(-0.8799661455640174, +0.26443630392015344), std::complex<T>(-0.8534754036851689, +0.37103893194823206),
		        std::complex<T>(-0.8171682088462721, +0.47856194922027806), std::complex<T>(-0.7700332930556816, +0.5874255426351151), std::complex<T>(-0.7105305456418792, +0.6982266265924527),
		        std::complex<T>(-0.6362427683267828, +0.811887504024635), std::complex<T>(-0.5430983056306306, +0.9299947824439877), std::complex<T>(-0.4232528745642629, +1.055755605227546),
		        std::complex<T>(-0.2566376987939318, +1.1979824335552132)},
		    1.0};
	case 23:
		return {{},
		    {std::complex<T>(-0.24976972022089572, -1.2028131878706978), std::complex<T>(-0.4126986617510148, -1.0653287944755134), std::complex<T>(-0.5304922463810198, -0.9439760364018306),
		        std::complex<T>(-0.6225903228771341, -0.830155830281298), std::complex<T>(-0.6965966033912708, -0.720734137475305), std::complex<T>(-0.7564660146829886, -0.6141594859476034),
		        std::complex<T>(-0.8045561642053178, -0.5095305912227259), std::complex<T>(-0.8423805948021129, -0.4062657948237603), std::complex<T>(-0.8709469395587415, -0.3039581993950041),
		        std::complex<T>(-0.8909283242471254, -0.20230246993812237), std::complex<T>(-0.9027564979912508, -0.10105343353140452), std::complex<T>(-0.9066732476324991, +0.),
		        std::complex<T>(-0.9027564979912508, +0.10105343353140452), std::complex<T>(-0.8909283242471254, +0.20230246993812237), std::complex<T>(-0.8709469395587415, +0.3039581993950041),
		        std::complex<T>(-0.8423805948021129, +0.4062657948237603), std::complex<T>(-0.8045561642053178, +0.5095305912227259), std::complex<T>(-0.7564660146829886, +0.6141594859476034),
		        std::complex<T>(-0.6965966033912708, +0.720734137475305), std::complex<T>(-0.6225903228771341, +0.830155830281298), std::complex<T>(-0.5304922463810198, +0.9439760364018306),
		        std::complex<T>(-0.4126986617510148, +1.0653287944755134), std::complex<T>(-0.24976972022089572, +1.2028131878706978)},
		    1.0};
	case 24:
		return {{},
		    {std::complex<T>(-0.24334813375248746, -1.2072986837319728), std::complex<T>(-0.4027853855197519, -1.0741951965186751), std::complex<T>(-0.518591457482032, -0.9569048385259057),
		        std::complex<T>(-0.6096221567378332, -0.8470292433077199), std::complex<T>(-0.6832565803536519, -0.7415032695091649), std::complex<T>(-0.7433392285088533, -0.6388084216222569),
		        std::complex<T>(-0.7921695462343489, -0.5380628490968016), std::complex<T>(-0.8312326466813242, -0.4386985933597306), std::complex<T>(-0.8615278304016355, -0.34032021126186246),
		        std::complex<T>(-0.8837358034555707, -0.24263352344013836), std::complex<T>(-0.8983105104397872, -0.14540561338736102), std::complex<T>(-0.9055312363372773, -0.0484400665404787),
		        std::complex<T>(-0.9055312363372773, +0.0484400665404787), std::complex<T>(-0.8983105104397872, +0.14540561338736102), std::complex<T>(-0.8837358034555707, +0.24263352344013836),
		        std::complex<T>(-0.8615278304016355, +0.34032021126186246), std::complex<T>(-0.8312326466813242, +0.4386985933597306), std::complex<T>(-0.7921695462343489, +0.5380628490968016),
		        std::complex<T>(-0.7433392285088533, +0.6388084216222569), std::complex<T>(-0.6832565803536519, +0.7415032695091649), std::complex<T>(-0.6096221567378332, +0.8470292433077199),
		        std::complex<T>(-0.518591457482032, +0.9569048385259057), std::complex<T>(-0.4027853855197519, +1.0741951965186751), std::complex<T>(-0.24334813375248746, +1.2072986837319728)},
		    1.0};
	case 25:
		return {{},
		    {std::complex<T>(-0.2373280669322027, -1.211476658382566), std::complex<T>(-0.3934529878191083, -1.082433927173832), std::complex<T>(-0.5073362861078469, -0.9689006305344871),
		        std::complex<T>(-0.5972898661335559, -0.8626676330388032), std::complex<T>(-0.6704827128029558, -0.7607348858167841), std::complex<T>(-0.730654927184997, -0.6616149647357752),
		        std::complex<T>(-0.7800496278186502, -0.5644441210349713), std::complex<T>(-0.820122604393688, -0.4686668574656967), std::complex<T>(-0.8518616886554026, -0.3738977875907597),
		        std::complex<T>(-0.8759497989677862, -0.2798521321771409), std::complex<T>(-0.8928551459883555, -0.1863068969804302), std::complex<T>(-0.9028833390228024, -0.093077131185103),
		        std::complex<T>(-0.9062073871811711, -0), std::complex<T>(-0.9028833390228024, 0.093077131185103), std::complex<T>(-0.8928551459883555, 0.1863068969804302),
		        std::complex<T>(-0.8759497989677862, 0.2798521321771409), std::complex<T>(-0.8518616886554026, 0.3738977875907597), std::complex<T>(-0.820122604393688, 0.4686668574656967),
		        std::complex<T>(-0.7800496278186502, 0.5644441210349713), std::complex<T>(-0.730654927184997, 0.6616149647357752), std::complex<T>(-0.6704827128029558, 0.7607348858167841),
		        std::complex<T>(-0.5972898661335559, 0.8626676330388032), std::complex<T>(-0.5073362861078469, 0.9689006305344871), std::complex<T>(-0.3934529878191083, 1.082433927173832),
		        std::complex<T>(-0.2373280669322027, 1.211476658382566)},
		    1.0};

	case 26:
		return {{},
		    {std::complex<T>(-0.9052324538106691, -0.04476325744604598), std::complex<T>(-0.9052324538106691, 0.04476325744604598), std::complex<T>(-0.8990666310927006, -0.1343571762381952),
		        std::complex<T>(-0.8990666310927006, 0.1343571762381952), std::complex<T>(-0.8866380387134746, -0.2241574214250259), std::complex<T>(-0.8866380387134746, 0.2241574214250259),
		        std::complex<T>(-0.8677449033146605, -0.3143158858637872), std::complex<T>(-0.8677449033146605, 0.3143158858637872), std::complex<T>(-0.8420632674571534, -0.4050083567357439),
		        std::complex<T>(-0.8420632674571534, 0.4050083567357439), std::complex<T>(-0.8091163277141674, -0.4964496178455549), std::complex<T>(-0.8091163277141674, 0.4964496178455549),
		        std::complex<T>(-0.7682227352042339, -0.5889163701545718), std::complex<T>(-0.7682227352042339, 0.5889163701545718), std::complex<T>(-0.7184081861805527, -0.6827850312360702),
		        std::complex<T>(-0.7184081861805527, 0.6827850312360702), std::complex<T>(-0.6582464599439503, -0.7785996445158956), std::complex<T>(-0.6582464599439503, 0.7785996445158956),
		        std::complex<T>(-0.5855487074007175, -0.8772069971587357), std::complex<T>(-0.5855487074007175, 0.8772069971587357), std::complex<T>(-0.4966735256116897, -0.9800651413347925),
		        std::complex<T>(-0.4966735256116897, 0.9800651413347925), std::complex<T>(-0.3846488473030819, -1.090112492627941), std::complex<T>(-0.3846488473030819, 1.090112492627941),
		        std::complex<T>(-0.2316706371468821, -1.215379413915129), std::complex<T>(-0.2316706371468821, 1.215379413915129)},
		    1};
	case 27:
		return {{},
		    {std::complex<T>(-0.902953178771725, -0.08626802840712901), std::complex<T>(-0.902953178771725, 0.08626802840712901), std::complex<T>(-0.8943426148399681, -0.1726574604972806),
		        std::complex<T>(-0.8943426148399681, 0.1726574604972806), std::complex<T>(-0.8798507910259237, -0.2592966419117326), std::complex<T>(-0.8798507910259237, 0.2592966419117326),
		        std::complex<T>(-0.8592550753423879, -0.3463287958555142), std::complex<T>(-0.8592550753423879, 0.3463287958555142), std::complex<T>(-0.832218944107149, -0.433922229672679),
		        std::complex<T>(-0.832218944107149, 0.433922229672679), std::complex<T>(-0.7982611032516844, -0.5222847983285389), std::complex<T>(-0.7982611032516844, 0.5222847983285389),
		        std::complex<T>(-0.756704442382851, -0.6116861401843384), std::complex<T>(-0.756704442382851, 0.6116861401843384), std::complex<T>(-0.7065894936424248, -0.702494568196811),
		        std::complex<T>(-0.7065894936424248, 0.702494568196811), std::complex<T>(-0.6465194176178479, -0.7952435076638122), std::complex<T>(-0.6465194176178479, 0.7952435076638122),
		        std::complex<T>(-0.5743574772259215, -0.8907637751274253), std::complex<T>(-0.5743574772259215, 0.8907637751274253), std::complex<T>(-0.4865556132188685, -0.9904855802302067),
		        std::complex<T>(-0.4865556132188685, 0.9904855802302067), std::complex<T>(-0.376326655113857, -1.097288864745255), std::complex<T>(-0.376326655113857, 1.097288864745255),
		        std::complex<T>(-0.2263419686360484, -1.219034774155181), std::complex<T>(-0.2263419686360484, 1.219034774155181), std::complex<T>(-0.9058095852880153, -0)},
		    1};
	case 28:
		return {{},
		    {std::complex<T>(-0.9049675732101182, -0.0416052215545288), std::complex<T>(-0.9049675732101182, 0.0416052215545288), std::complex<T>(-0.8996411275045194, -0.1248697892482228),
		        std::complex<T>(-0.8996411275045194, 0.1248697892482228), std::complex<T>(-0.888916013939554, -0.2082995792271283), std::complex<T>(-0.888916013939554, 0.2082995792271283),
		        std::complex<T>(-0.8726427458122928, -0.292014607131267), std::complex<T>(-0.8726427458122928, 0.292014607131267), std::complex<T>(-0.8505836134335824, -0.3761510891463731),
		        std::complex<T>(-0.8505836134335824, 0.3761510891463731), std::complex<T>(-0.8223937714882537, -0.4608711099673119), std::complex<T>(-0.8223937714882537, 0.4608711099673119),
		        std::complex<T>(-0.7875904240739772, -0.5463765734839992), std::complex<T>(-0.7875904240739772, 0.5463765734839992), std::complex<T>(-0.7455025768323552, -0.6329308506203905),
		        std::complex<T>(-0.7455025768323552, 0.6329308506203905), std::complex<T>(-0.6951863773002307, -0.7208948493705689), std::complex<T>(-0.6951863773002307, 0.7208948493705689),
		        std::complex<T>(-0.6352738927047991, -0.8107920823138458), std::complex<T>(-0.6352738927047991, 0.8107920823138458), std::complex<T>(-0.563678318141969, -0.9034382679629595),
		        std::complex<T>(-0.563678318141969, 0.9034382679629595), std::complex<T>(-0.4769399312221216, -1.000237160109307), std::complex<T>(-0.4769399312221216, 1.000237160109307),
		        std::complex<T>(-0.3684454882916625, -1.104013123976685), std::complex<T>(-0.3684454882916625, 1.104013123976685), std::complex<T>(-0.2213123988374071, -1.222466844702999),
		        std::complex<T>(-0.2213123988374071, 1.222466844702999)},
		    1};
	case 29:
		return {{},
		    {std::complex<T>(-0.902984987283314, -0.08038736507159364), std::complex<T>(-0.902984987283314, 0.08038736507159364), std::complex<T>(-0.8955109542725674, -0.1608728856794533),
		        std::complex<T>(-0.8955109542725674, 0.1608728856794533), std::complex<T>(-0.882948347730274, -0.2415595757409829), std::complex<T>(-0.882948347730274, 0.2415595757409829),
		        std::complex<T>(-0.8651310147344007, -0.3225607653027801), std::complex<T>(-0.8651310147344007, 0.3225607653027801), std::complex<T>(-0.8418104539477226, -0.4040069035733456),
		        std::complex<T>(-0.8418104539477226, 0.4040069035733456), std::complex<T>(-0.8126366465296433, -0.4860548082176107), std::complex<T>(-0.8126366465296433, 0.4860548082176107),
		        std::complex<T>(-0.7771274827532496, -0.5689011910831927), std::complex<T>(-0.7771274827532496, 0.5689011910831927), std::complex<T>(-0.734619392424445, -0.6528037891258393),
		        std::complex<T>(-0.734619392424445, 0.6528037891258393), std::complex<T>(-0.6841845443912495, -0.7381166816974976), std::complex<T>(-0.6841845443912495, 0.7381166816974976),
		        std::complex<T>(-0.6244832514209309, -0.8253540734258508), std::complex<T>(-0.6244832514209309, 0.8253540734258508), std::complex<T>(-0.5534764884367008, -0.9153173617161553),
		        std::complex<T>(-0.5534764884367008, 0.9153173617161553), std::complex<T>(-0.4677882006601411, -1.009385109576755), std::complex<T>(-0.4677882006601411, 1.009385109576755),
		        std::complex<T>(-0.3609690417504163, -1.110328771840205), std::complex<T>(-0.3609690417504163, 1.110328771840205), std::complex<T>(-0.2165558325670751, -1.225696622049645),
		        std::complex<T>(-0.2165558325670751, 1.225696622049645), std::complex<T>(-0.9054659433863002, -0)},
		    1};
	case 30:
		return {{},
		    {std::complex<T>(-0.9047314102630178, -0.03886340417354411), std::complex<T>(-0.9047314102630178, 0.03886340417354411), std::complex<T>(-0.9000838040479108, -0.1166343309248629),
		        std::complex<T>(-0.9000838040479108, 0.1166343309248629), std::complex<T>(-0.8907336812630257, -0.1945396356500104), std::complex<T>(-0.8907336812630257, 0.1945396356500104),
		        std::complex<T>(-0.8765678910829173, -0.2726758758280575), std::complex<T>(-0.8765678910829173, 0.2726758758280575), std::complex<T>(-0.8574078831584224, -0.3511509221338205),
		        std::complex<T>(-0.8574078831584224, 0.3511509221338205), std::complex<T>(-0.8329975793537641, -0.430090397753356), std::complex<T>(-0.8329975793537641, 0.430090397753356),
		        std::complex<T>(-0.8029841409610239, -0.5096465917164855), std::complex<T>(-0.8029841409610239, 0.5096465917164855), std::complex<T>(-0.7668877489397753, -0.590011622641491),
		        std::complex<T>(-0.7668877489397753, 0.590011622641491), std::complex<T>(-0.7240531518527603, -0.6714381064379565), std::complex<T>(-0.7240531518527603, 0.6714381064379565),
		        std::complex<T>(-0.6735686810408362, -0.7542737761710706), std::complex<T>(-0.6735686810408362, 0.7542737761710706), std::complex<T>(-0.6141221167710863, -0.8390240541799485),
		        std::complex<T>(-0.6141221167710863, 0.8390240541799485), std::complex<T>(-0.5437201201492814, -0.9264767150507045), std::complex<T>(-0.5437201201492814, 0.9264767150507045),
		        std::complex<T>(-0.4590659561665237, -1.017986291213988), std::complex<T>(-0.4590659561665237, 1.017986291213988), std::complex<T>(-0.3538649882181192, -1.116273788286472),
		        std::complex<T>(-0.3538649882181192, 1.116273788286472), std::complex<T>(-0.2120492126608843, -1.22874248579382), std::complex<T>(-0.2120492126608843, 1.22874248579382)},
		    1};
	default: return {{}, {}, 1.f};
	}
}

namespace internal {

template <typename T>
struct biquad_section {
	T a0, a1, a2;
	T b0, b1, b2;
};

template <typename T>
struct zero_pole_pairs {
	std::complex<T> p1, p2, z1, z2;
};

// 辅助函数：计算复数向量的乘积
template <typename T>
std::complex<T> product(const std::vector<std::complex<T>> &v) {
	std::complex<T> result(1, 0);
	for (const auto &x : v) { result *= x; }
	return result;
}

// 辅助函数：判断复数是否为实数
template <typename T>
bool isreal(const std::complex<T> &c) {
	return std::abs(c.imag()) < std::numeric_limits<T>::epsilon() * 100;
}

// 辅助函数：计算复数的绝对值
template <typename T>
T cabs(const std::complex<T> &c) {
	return std::abs(c);
}

// bilinear 变换
template <typename T>
zpk<T> bilinear(const zpk<T> &filter, T fs) {
	const T fs2 = T(2.0) * fs;
	zpk<T> result;

	// 转换 zeros
	result.zeros.resize(filter.zeros.size());
	for (size_t i = 0; i < filter.zeros.size(); ++i) { result.zeros[i] = (fs2 + filter.zeros[i]) / (fs2 - filter.zeros[i]); }

	// 转换 poles
	result.poles.resize(filter.poles.size());
	for (size_t i = 0; i < filter.poles.size(); ++i) { result.poles[i] = (fs2 + filter.poles[i]) / (fs2 - filter.poles[i]); }

	// 调整 zeros 大小
	result.zeros.resize(result.poles.size(), std::complex<T>(-1));

	// 计算增益
	auto num = std::complex<T>(1.0);
	auto den = std::complex<T>(1.0);
	for (size_t i = 0; i < filter.zeros.size(); ++i) { num *= (fs2 - filter.zeros[i]); }

	for (size_t i = 0; i < filter.poles.size(); ++i) den *= (fs2 - filter.poles[i]);

	result.gain = filter.gain * (num / den).real();
	return result;
}

// zpk2tf_poly 函数
template <typename T>
std::array<T, 3> zpk2tf_poly(const std::complex<T> &x, const std::complex<T> &y) {
	return {T(1), -(x.real() + y.real()), x.real() * y.real() - x.imag() * y.imag()};
}

// zpk2tf 函数
template <typename T>
biquad_section<T> zpk2tf(const zero_pole_pairs<T> &pairs, T k) {
	auto zz = zpk2tf_poly(pairs.z1, pairs.z2);
	auto pp = zpk2tf_poly(pairs.p1, pairs.p2);
	return {pp[0], pp[1], pp[2], k * zz[0], k * zz[1], k * zz[2]};
}

// cplxreal 函数
template <typename T>
std::vector<std::complex<T>> cplxreal(const std::vector<std::complex<T>> &list) {
	std::vector<std::complex<T>> x = list;
	std::sort(x.begin(), x.end(), [](const std::complex<T> &a, const std::complex<T> &b) { return a.real() < b.real(); });

	T tol = std::numeric_limits<T>::epsilon() * 100;
	std::vector<std::complex<T>> result = x;

	for (size_t i = result.size(); i > 1; i--) {
		if (!isreal(result[i - 1]) && !isreal(result[i - 2])) {
			if (std::abs(result[i - 1].real() - result[i - 2].real()) < tol && std::abs(result[i - 1].imag() + result[i - 2].imag()) < tol) {
				result.erase(result.begin() + i - 1);
				result[i - 2] = std::complex<T>(result[i - 2].real(), std::abs(result[i - 2].imag()));
			}
		}
	}
	return result;
}

// 辅助函数：复数平方
template <typename T>
std::complex<T> csqr(const std::complex<T> &z) {
	return z * z;
}

// 辅助函数：复数平方根
template <typename T>
std::complex<T> csqrt(const std::complex<T> &z) {
	return std::sqrt(z);
}

// 辅助函数：连接两个向量
template <typename T>
std::vector<T, std::allocator<T>> concatenate(const std::vector<T> &a, const std::vector<T> &b) {
	std::vector<T, std::allocator<T>> result;
	result.reserve(a.size() + b.size());
	result.insert(result.end(), a.begin(), a.end());
	result.insert(result.end(), b.begin(), b.end());
	return result;
}

template <typename T>
size_t nearest_real_or_complex(const std::vector<std::complex<T>> &list, const std::complex<T> &val, bool mustbereal = true) {
	// 创建过滤后的列表
	std::vector<std::complex<T>> filtered;
	filtered.reserve(list.size()); // 预分配内存以提高性能

	// 根据条件筛选元素
	for (const auto &v : list) {
		if (isreal(v) == mustbereal) { filtered.push_back(v); }
	}

	// 检查是否找到符合条件的元素
	assert(!filtered.empty() && "No matching elements found");
	if (filtered.empty()) { return std::numeric_limits<size_t>::max(); }

	// 找到最近的元素
	size_t minidx = 0;
	T minval = std::abs(val - filtered[0]);

	for (size_t i = 1; i < filtered.size(); i++) {
		T newminval = std::abs(val - filtered[i]);
		if (newminval < minval) {
			minval = newminval;
			minidx = i;
		}
	}

	return minidx;
}

template <typename T>
int countreal(const std::vector<std::complex<T>> &list) {
	int nreal = 0;
	const T epsilon = std::numeric_limits<T>::epsilon();

	// 计算实数的个数
	for (const auto &c : list) {
		if (std::abs(c.imag()) < epsilon) { nreal++; }
	}

	return nreal;
}

// 低通到低通变换
template <typename T>
zpk<T> lp2lp_zpk(const zpk<T> &filter, T wo) {
	zpk<T> result;

	// 转换零点
	result.zeros.resize(filter.zeros.size());
	for (size_t i = 0; i < filter.zeros.size(); ++i) { result.zeros[i] = wo * filter.zeros[i]; }

	// 转换极点
	result.poles.resize(filter.poles.size());
	for (size_t i = 0; i < filter.poles.size(); ++i) { result.poles[i] = wo * filter.poles[i]; }

	// 计算增益
	result.gain = filter.gain * std::pow((double)wo, static_cast<double>(filter.poles.size() - filter.zeros.size()));

	return result;
}

// 低通到高通变换
template <typename T>
zpk<T> lp2hp_zpk(const zpk<T> &filter, T wo) {
	zpk<T> result;

	// 转换零点
	result.zeros.resize(filter.zeros.size());
	for (size_t i = 0; i < filter.zeros.size(); ++i) { result.zeros[i] = wo / filter.zeros[i]; }

	// 转换极点
	result.poles.resize(filter.poles.size());
	for (size_t i = 0; i < filter.poles.size(); ++i) { result.poles[i] = wo / filter.poles[i]; }

	// 添加额外的零点
	result.zeros.resize(result.poles.size(), std::complex<T>(0));

	// 计算增益
	std::complex<T> num = product(filter.zeros);
	std::complex<T> den = product(filter.poles);
	result.gain = filter.gain * (-num / -den).real();

	return result;
}

// 低通到带通变换
template <typename T>
zpk<T> lp2bp_zpk(const zpk<T> &filter, T wo, T bw) {
	zpk<T> lowpass;

	// 计算中间结果
	lowpass.zeros.resize(filter.zeros.size());
	lowpass.poles.resize(filter.poles.size());
	T scale = bw * T(0.5);

	for (size_t i = 0; i < filter.zeros.size(); ++i) { lowpass.zeros[i] = scale * filter.zeros[i]; }
	for (size_t i = 0; i < filter.poles.size(); ++i) { lowpass.poles[i] = scale * filter.poles[i]; }

	zpk<T> result;
	std::complex<T> wo2 = std::complex<T>(wo * wo, 0);

	// 计算新的零点
	std::vector<std::complex<T>> z1, z2;
	z1.reserve(lowpass.zeros.size());
	z2.reserve(lowpass.zeros.size());

	for (const auto &z : lowpass.zeros) {
		std::complex<T> temp = csqrt(csqr(z) - wo2);
		z1.push_back(z + temp);
		z2.push_back(z - temp);
	}
	result.zeros = concatenate(z1, z2);

	// 计算新的极点
	std::vector<std::complex<T>> p1, p2;
	p1.reserve(lowpass.poles.size());
	p2.reserve(lowpass.poles.size());

	for (const auto &p : lowpass.poles) {
		std::complex<T> temp = csqrt(csqr(p) - wo2);
		p1.push_back(p + temp);
		p2.push_back(p - temp);
	}
	result.poles = concatenate(p1, p2);

	// 添加额外的零点
	result.zeros.resize(result.zeros.size() + filter.poles.size() - filter.zeros.size(), std::complex<T>(0));

	// 计算增益
	result.gain = filter.gain * std::pow(bw, static_cast<T>(filter.poles.size() - filter.zeros.size()));

	return result;
}

// 低通到带阻变换
template <typename T>
zpk<T> lp2bs_zpk(const zpk<T> &filter, T wo, T bw) {
	zpk<T> highpass;
	T scale = bw * T(0.5);

	// 计算中间结果
	highpass.zeros.resize(filter.zeros.size());
	highpass.poles.resize(filter.poles.size());

	for (size_t i = 0; i < filter.zeros.size(); ++i) { highpass.zeros[i] = scale / filter.zeros[i]; }
	for (size_t i = 0; i < filter.poles.size(); ++i) { highpass.poles[i] = scale / filter.poles[i]; }

	zpk<T> result;
	std::complex<T> wo2 = std::complex<T>(wo * wo, 0);

	// 计算新的零点和极点
	std::vector<std::complex<T>> z1, z2, p1, p2;
	z1.reserve(highpass.zeros.size());
	z2.reserve(highpass.zeros.size());
	p1.reserve(highpass.poles.size());
	p2.reserve(highpass.poles.size());

	for (const auto &z : highpass.zeros) {
		std::complex<T> temp = csqrt(csqr(z) - wo2);
		z1.push_back(z + temp);
		z2.push_back(z - temp);
	}
	for (const auto &p : highpass.poles) {
		std::complex<T> temp = csqrt(csqr(p) - wo2);
		p1.push_back(p + temp);
		p2.push_back(p - temp);
	}

	result.zeros = concatenate(z1, z2);
	result.poles = concatenate(p1, p2);

	// 添加额外的零点
	size_t extra_zeros = filter.poles.size() - filter.zeros.size();
	result.zeros.resize(result.zeros.size() + extra_zeros, std::complex<T>(0, wo));
	result.zeros.resize(result.zeros.size() + extra_zeros, std::complex<T>(0, -wo));

	// 计算增益
	std::complex<T> num = product(filter.zeros);
	std::complex<T> den = product(filter.poles);
	result.gain = filter.gain * (-num / -den).real();

	return result;
}

// warp_freq 函数
template <typename T>
T warp_freq(T frequency, T fs = 2.0) {
	const T pi = T(3.14159265358979323846);
	frequency = T(2.0) * frequency / fs;
	fs = T(2.0);
	return T(2.0) * fs * std::tan(pi * frequency / fs);
}

}; // namespace internal

// 低通滤波器
template <typename T>
zpk<T> iir_lowpass(const zpk<T> &filter, T frequency, T fs = T(2.0f), T gain = T(1.0f)) {
	T warped = internal::warp_freq(frequency, fs);

	zpk<T> result = filter;
	result = internal::lp2lp_zpk(result, T(warped));
	result = internal::bilinear(result, T(2.0f));
	return result;
}

// 高通滤波器
template <typename T>
zpk<T> iir_highpass(const zpk<T> &filter, T frequency, T fs = T(2.0)) {
	T warped = internal::warp_freq(frequency, fs);

	zpk<T> result = filter;
	result = internal::lp2hp_zpk(result, warped);
	result = internal::bilinear(result, T(2.0));
	return result;
}

// 带通滤波器
template <typename T>
zpk<T> iir_bandpass(const zpk<T> &filter, T lowfreq, T highfreq, T fs = T(2.0)) {
	T warpedlow = internal::warp_freq(lowfreq, fs);
	T warpedhigh = internal::warp_freq(highfreq, fs);

	zpk<T> result = filter;
	result = internal::lp2bp_zpk(result, std::sqrt(warpedlow * warpedhigh), warpedhigh - warpedlow);
	result = internal::bilinear(result, T(2.0));
	return result;
}

// 带阻滤波器
template <typename T>
zpk<T> iir_bandstop(const zpk<T> &filter, T lowfreq, T highfreq, T fs = T(2.0)) {
	T warpedlow = internal::warp_freq(lowfreq, fs);
	T warpedhigh = internal::warp_freq(highfreq, fs);

	zpk<T> result = filter;
	result = internal::lp2bs_zpk(result, std::sqrt(warpedlow * warpedhigh), warpedhigh - warpedlow);
	result = internal::bilinear(result, T(2.0));
	return result;
}

template <typename T>
zpk<T> iir_butterworth_lowshelf(T freq, T gain, uint32_t N = 2, T fs = 2.0) {
	T warped = internal::warp_freq(freq, fs);
	double g = std::pow(10, gain / 20);

	zpk<T> result;
	result.zeros.resize(N);
	result.poles.resize(N);

	for (uint32_t m = 0; m < N; ++m) {
		T alpha = M_PI * (0.5 - (2 * m + 1.0) / 2 / N);

		result.poles[m] = -std::exp(std::complex<T>(0, alpha));
		result.zeros[m] = std::complex<T>(std::pow(g, 1.0 / N)) * result.poles[m];

		result.poles[m] *= std::pow(g, -0.5 / N) * warped;
		result.zeros[m] *= std::pow(g, -0.5 / N) * warped;
	}
	result.gain = 1;
	return internal::bilinear(result, T(2.0));
}

template <typename T>
zpk<T> iir_butterworth_highshelf(T freq, T gain, uint32_t N = 2, T fs = 2.0) {
	T warped = internal::warp_freq(freq, fs);

	T g = std::pow(10, -gain / 20);

	zpk<T> result;
	result.zeros.resize(N);
	result.poles.resize(N);

	for (uint32_t m = 0; m < N; ++m) {
		T alpha = M_PI * (0.5 - (2 * m + 1.0) / 2 / N);
		result.poles[m] = -std::exp(std::complex<T>(0, alpha));
		result.zeros[m] = std::complex<T>(std::pow(g, 1.0 / N)) * result.poles[m];

		result.poles[m] *= std::pow(g, -0.5 / N) * warped;
		result.zeros[m] *= std::pow(g, -0.5 / N) * warped;
	}
	result.gain = 1 / g;
	return internal::bilinear(result, T(2.0));
}

template <typename T>
zpk<T> iir_butterworth_flattilt(T gain) {
	T freq = 0.99;
	T g = std::pow(10, -gain / 20);
	zpk<T> result;
	result.gain = 1;
	{
		auto warped = internal::warp_freq(freq);
		T alpha = M_PI * (0.5 - (2 * 0 + 1.0) / 2 / 1);
		result.poles.push_back(-std::exp(std::complex<T>(0, alpha)));
		result.zeros.push_back(std::complex<T>(std::pow(g, 1.0 / 1)) * result.poles[result.poles.size() - 1]);
		result.poles.back() *= std::pow(g, -0.5) * warped;
		result.zeros.back() *= std::pow(g, -0.5) * warped;
		result.gain *= 1 / g;
	}
	g = std::pow(10, gain / 20);
	while (freq > 5.0 / 24000) {
		auto warped = internal::warp_freq(freq);
		T alpha = M_PI * (0.5 - (2 * 0 + 1.0) / 2 / 1);
		result.poles.push_back(-std::exp(std::complex<T>(0, alpha)));
		result.zeros.push_back(std::complex<T>(std::pow(g, 1.0 / 1)) * result.poles[result.poles.size() - 1]);
		result.poles.back() *= std::pow(g, -0.5) * warped;
		result.zeros.back() *= std::pow(g, -0.5) * warped;
		freq /= 4;
	}
	return internal::bilinear(result, T(2.0));
}

template <typename T> // 这玩意看起来数值不稳定，但总之代码先留着
zpk<T> spectral_tilt(int N, double f0, double bw, double alpha, double SR = 2.0) {
	const double EPS = 1e-20;
	alpha /= N;
	double w0 = 2 * M_PI * f0;
	double f1 = f0 + bw;
	double r = std::pow(f1 / f0, 1.0 / (N - 1));
	double deltaT = 1.0 / SR;
	double tan_w0_T2 = std::tan(w0 * deltaT / 2.0); // prewarp denom

	zpk<T> zp;
	zp.zeros.reserve(N);
	zp.poles.reserve(N);

	auto prewarp = [&](double w) -> double {
		// 对应 Faust: prewarp(w,SR,w0) = w0 * tan(w*T/2)/tan(w0*T/2)
		return w0 * std::tan(w * deltaT / 2.0) / tan_w0_T2;
	};

	for (int i = 0; i < N; ++i) {
		// 模拟 s 平面上的 “极点”/“零点”
		double mz = w0 * std::pow(r, -alpha + i);
		double mp = w0 * std::pow(r, i);

		// 先做 Faust 里的 prewarp
		double mzh = prewarp(mz);
		double mph = prewarp(mp);

		// 模拟一阶截面 H(s) = (s + mzh)/(s + mph)
		// 双线性变换： s = 2/T*(1 - z^-1)/(1 + z^-1)
		// 得到数字域分子分母系数：
		//   B(z) = [b0, b1] = [2/T + mzh,  -2/T + mzh]
		//   A(z) = [a0, a1] = [2/T + mph,  -2/T + mph]
		double c = 2.0 / deltaT;
		double b0 = c + mzh;
		double b1 = -c + mzh;
		double a0 = c + mph;
		double a1 = -c + mph;

		// 求零极点：分子 b0 + b1 z^-1 => z_zero = −b1/b0
		//           分母 a0 + a1 z^-1 => z_pole = −a1/a0
		double z_z = -b1 / b0;
		double z_p = -a1 / a0;

		zp.zeros.push_back(z_z);
		zp.poles.push_back(z_p);
	}
	return zp;
}

template <typename T>
using filter_coeffs = std::array<T, 5>;

template <typename T> // T
std::vector<filter_coeffs<T>> to_sos(const zpk<T> &filter, T wNorm = 0.0) {
	// 空滤波器的特殊情况处理
	if (filter.poles.empty() && filter.zeros.empty()) {
		filter_coeffs<T> section;
		section[0] = filter.gain; // b0 = gain
		section[1] = T(0);        // b1 = 0
		section[2] = T(0);        // b2 = 0
		section[3] = T(0);        // -a1 = 0
		section[4] = T(0);        // -a2 = 0
		return {section};
	}

	// 复制输入滤波器并调整大小
	zpk<double> filt;
	for (uint32_t i = 0; i < filter.poles.size(); i++) {
		filt.zeros.push_back(std::complex<double>(filter.zeros[i].real(), filter.zeros[i].imag()));
		filt.poles.push_back(std::complex<double>(filter.poles[i].real(), filter.poles[i].imag()));
		filt.gain = filter.gain;
	}
	size_t length = std::max(filter.poles.size(), filter.zeros.size());
	filt.poles.resize(length, std::complex<T>(0));
	filt.zeros.resize(length, std::complex<T>(0));

	// 计算所需的节数
	size_t n_sections = (length + 1) / 2;
	if (length & 1) {
		filt.zeros.push_back(std::complex<T>(0));
		filt.poles.push_back(std::complex<T>(0));
	}

	// 转换成实数对
	filt.zeros = internal::cplxreal(filt.zeros);
	filt.poles = internal::cplxreal(filt.poles);

	// 存储结果
	std::vector<filter_coeffs<T>> result(n_sections);
	std::vector<internal::zero_pole_pairs<double>> pairs(n_sections);

	// 处理每个节
	for (size_t si = 0; si < n_sections; si++) {
		// 找到最差极点
		size_t worstidx = 0;
		double worstval = std::abs(1.0 - std::abs(filt.poles[0]));
		for (size_t i = 1; i < filt.poles.size(); i++) {
			double val = std::abs(1.0 - std::abs(filt.poles[i]));
			if (val < worstval) {
				worstidx = i;
				worstval = val;
			}
		}

		std::complex<double> p1 = filt.poles[worstidx];
		filt.poles.erase(filt.poles.begin() + worstidx);

		std::complex<double> z1, p2, z2;
		if (internal::isreal(p1) && internal::countreal(filt.poles) == 0) {
			// 处理实数极点
			size_t z1_idx = internal::nearest_real_or_complex(filt.zeros, p1, true);
			z1 = filt.zeros[z1_idx];
			filt.zeros.erase(filt.zeros.begin() + z1_idx);
			p2 = z2 = std::complex<T>(0);
		} else {
			// 处理复数极点
			size_t z1_idx;
			if (!internal::isreal(p1) && internal::countreal(filt.zeros) == 1) {
				z1_idx = internal::nearest_real_or_complex(filt.zeros, p1, false);
			} else {
				size_t minidx = 0;
				T minval = internal::cabs(p1 - filt.zeros[0]);
				for (size_t i = 1; i < filt.zeros.size(); i++) {
					T newminval = internal::cabs(p1 - filt.zeros[i]);
					if (newminval < minval) {
						minidx = i;
						minval = newminval;
					}
				}
				z1_idx = minidx;
			}
			z1 = filt.zeros[z1_idx];
			filt.zeros.erase(filt.zeros.begin() + z1_idx);
			if (!internal::isreal(p1)) {
				if (!internal::isreal(z1)) {
					p2 = cconj(p1);
					z2 = cconj(z1);
				} else {
					p2 = cconj(p1);
					size_t z2_idx = internal::nearest_real_or_complex(filt.zeros, p1, true);
					z2 = filt.zeros[z2_idx];
					// TESTO_ASSERT(isreal(z2));
					filt.zeros.erase(filt.zeros.begin() + z2_idx);
				}
			} else {
				size_t p2_idx;
				size_t z2_idx;
				if (!internal::isreal(z1)) {
					z2 = cconj(z1);
					p2_idx = internal::nearest_real_or_complex(filt.zeros, p1, true);
					p2 = filt.poles[p2_idx];
					// TESTO_ASSERT(isreal(p2));
				} else {
					size_t worstidx = 0;
					double worstval = abs(std::abs(filt.poles[0]) - 1.0);
					for (size_t i = 1; i < filt.poles.size(); i++) {
						double val = abs(std::abs(filt.poles[i]) - 1.0);
						if (val < worstval) {
							worstidx = i;
							worstval = val;
						}
					}
					p2_idx = worstidx;
					p2 = filt.poles[p2_idx];

					// TESTO_ASSERT(isreal(p2));
					z2_idx = internal::nearest_real_or_complex(filt.zeros, p2, true);
					z2 = filt.zeros[z2_idx];
					// TESTO_ASSERT(isreal(z2));
					filt.zeros.erase(filt.zeros.begin() + z2_idx);
				}
				filt.poles.erase(filt.poles.begin() + p2_idx);
			}
		}

		pairs[si].p1 = p1;
		pairs[si].p2 = p2;
		pairs[si].z1 = z1;
		pairs[si].z2 = z2;

		// 转换为传递函数系数
		double gain = (si == 0) ? filt.gain : T(1);
		// 计算二阶节系数
		auto tf = internal::zpk2tf(pairs[si], 1.0);

		// 存储系数，注意符号转换
		filter_coeffs<T> &section = result[n_sections - 1 - si];
		section[0] = tf.b0 / tf.a0;  // b0
		section[1] = tf.b1 / tf.a0;  // b1
		section[2] = tf.b2 / tf.a0;  // b2
		section[3] = -tf.a1 / tf.a0; // -a1
		section[4] = -tf.a2 / tf.a0; // -a2

		if (wNorm == 0) gain = (1 - section[3] - section[4]) / abs(section[0] + section[1] + section[2]);
		else if (wNorm == 1)
			gain = (1 + section[3] - section[4]) / abs(section[0] - section[1] + section[2]);
		else {
			std::complex<T> tmp = std::exp(std::complex<T>(0, -1) * wNorm * (T)M_PI);
			gain = std::abs((std::complex<T>(1, 0) - section[3] * tmp - section[4] * tmp * tmp) / (section[0] + section[1] * tmp + section[2] * tmp * tmp));
		}

		section[0] *= gain; // b0
		section[1] *= gain; // b1
		section[2] *= gain; // b2
	}

	// auto a = filter.gain;
	// for (int i = 0; i < result.size(); ++i)
	//     a/=(1-result[i][3]-result[i][4])/4;

	return std::move(result);
}

template <typename T>
filter_coeffs<T> normalize_section(filter_coeffs<T> section, T wNorm) {
	T gain = 1.0;
	if (wNorm == 0) gain = (1 - section[3] - section[4]) / abs(section[0] + section[1] + section[2]);
	else if (wNorm == 1)
		gain = (1 + section[3] - section[4]) / abs(section[0] - section[1] + section[2]);
	else {
		std::complex<T> tmp = std::exp(std::complex<T>(0, -1) * wNorm * (T)M_PI);
		gain = std::abs((std::complex<T>(1, 0) - section[3] * tmp - section[4] * tmp * tmp) / (section[0] + section[1] * tmp + section[2] * tmp * tmp));
	}
	section[0] *= gain; // b0
	section[1] *= gain; // b1
	section[2] *= gain; // b2
	return section;
}

template <typename T>
std::vector<filter_coeffs<T>> sos_normalize(std::vector<filter_coeffs<T>> sos) {
	for (size_t i = 0; i < sos.size(); ++i) { normalize_section(sos[i]); }
	return sos;
}

}; // namespace ZpkFilter

template <typename T>
class IIR_DirectForm {
public:
	constexpr static uint32_t order = 2;
	using value_type = T;
	using coeff_array = std::array<T, 2 * order + 1>;
	using sample_array = std::array<T, 2 * order + 1>;
	static constexpr uint32_t filter_order = order;

	constexpr IIR_DirectForm() noexcept : coeffs_{}, xys{} { coeffs_[0] = 1.0f; }

	constexpr explicit IIR_DirectForm(const coeff_array &coeffs) noexcept : xys{} {
		for (size_t i = 0; i < 2 * order + 1; ++i) { coeffsTarget_[i] = coeffs[i]; }
		sync_all();
	}

	[[nodiscard]] constexpr uint32_t get_order() const noexcept { return order; }

	FORCE_INLINE T process(T input) noexcept {
		// b0*x[n]
		T output = coeffs_[0] * input;

		auto coeffSimd = xsimd::load_unaligned(&coeffs_[1]);
		auto xysSimd = xsimd::load_aligned(&xys[0]);
		// b1*x[n-1] + b2*x[n-2] + ... + bN*x[n-N]
		output += xsimd::reduce_add(coeffSimd * xysSimd);

		// 更新历史记录
		// 移动x历史记录
		xsimd::rotate_right<1>(xysSimd).store_aligned(&xys[0]);

		xys[0] = input;
		xys[2] = output;

		auto coeffTargetSimd = xsimd::load_unaligned(&coeffsTarget_[1]);
		coeffSimd = coeffSimd + (coeffTargetSimd - coeffSimd) * 0.004f;
		coeffSimd.store_unaligned(&coeffs_[1]);
		coeffs_[0] = coeffs_[0] + (coeffsTarget_[0] - coeffs_[0]) * 0.004f;

		return output;
		return coeffs_[0];
	}

	T operator()(T input) noexcept { return process(input); }

	void update(const coeff_array &coeffs) noexcept {
		for (size_t i = 0; i < 2 * order + 1; ++i) { coeffsTarget_[i] = coeffs[i]; }
		// reset();
	}

	void update(coeff_array &&coeffs) noexcept {
		coeffsTarget_ = std::move(coeffs);
		// reset();
	}

	void reset() noexcept { std::fill(xys.begin(), xys.end(), T{0}); }

	void sync_all() { coeffs_ = coeffsTarget_; }

	[[nodiscard]] const coeff_array &coefficients() const noexcept { return coeffs_; }

private:
	alignas(64) coeff_array coeffs_;
	alignas(64) coeff_array coeffsTarget_;
	alignas(64) sample_array xys;
};

template <typename T, uint32_t order = 2, bool gradual = false>
class IIR_DirectFormIITransposed {
public:
	using value_type = T;
	using coeff_array = std::array<T, 2 * order + 1>;
	using coeff_gradient_array = std::array<GradualLimitLinear<T, gradual>, 2 * order + 1>;
	using state_array = std::array<T, 2 * (order + 1)>;
	static constexpr uint32_t filter_order = order;

	constexpr IIR_DirectFormIITransposed() noexcept : coeffs_{}, state_{} { coeffs_[0] = 1.0f, coeffs_[0].sync(); }

	constexpr explicit IIR_DirectFormIITransposed(const coeff_array &coeffs) noexcept : state_{} {
		for (size_t i = 0; i < order * 2 + 1; ++i) { coeffs_[i] = coeffs[i], coeffs_[i].sync(); }
	}

	[[nodiscard]] constexpr uint32_t get_order() const noexcept { return order; }

	FORCE_INLINE T process(T input) noexcept {
		if constexpr (gradual) {
			if (coeffs_[0].isUpdating()) {
				for (size_t i = 0; i < coeffs_.size(); ++i) coeffs_[i].forceUpdate();
			}
		}
		const T output = coeffs_[0].getCurrentValue() * input + state_.front();

		auto state_it = state_.begin();
		auto next_state = std::next(state_it);

		for (size_t i = 1; i < order + 1; ++i, ++state_it, ++next_state) {
			const T coeffA = coeffs_[i].getCurrentValue(), coeffB = coeffs_[order + i].getCurrentValue();
			*state_it = coeffA * input + coeffB * output + *next_state;
		}

		return output;
		// return coeffs_[0];
	}

	FORCE_INLINE void processBatch(T *inputs, T *outputs, int size) noexcept {
		LOOP_UNROLL(4)
		for (int j = 0; j < size; ++j) {
			if constexpr (gradual) {
				if (coeffs_[0].isUpdating()) {
					for (size_t i = 0; i < coeffs_.size(); ++i) coeffs_[i].forceUpdate();
				}
			}
			const T input = inputs[j];
			const T output = coeffs_[0].getCurrentValue() * input + state_.front();

			auto state_it = state_.begin();
			auto next_state = std::next(state_it);

			LOOP_UNROLL_FULL()
			for (size_t i = 1; i < order + 1; ++i, ++state_it, ++next_state) {
				const T coeffA = coeffs_[i].getCurrentValue(), coeffB = coeffs_[order + i].getCurrentValue();
				*state_it = coeffA * input + coeffB * output + *next_state;
			}

			outputs[j] = output;
		}
	}

#if defined(__aarch64__) && 1
#include <arm_neon.h>
	FORCE_INLINE std::pair<T, T> process_pair(const T inputL, const T inputR) noexcept {
		if constexpr (gradual) {
			if (coeffs_[0].isUpdating()) {
				for (size_t i = 0; i < coeffs_.size(); ++i) coeffs_[i].forceUpdate();
			}
		}
		if constexpr (sizeof(T) == 4) {
			const float32x2_t input = {inputL, inputR};
			const float32x2_t output = coeffs_[0].getCurrentValue() * input + vld1_f32(&state_[0]);
			LOOP_UNROLL_FULL()
			for (int i = 1; i < order + 1; ++i) {
				const float32x2_t coeffA = vdup_n_f32(coeffs_[i].getCurrentValue());
				const float32x2_t coeffB = vdup_n_f32(coeffs_[order + i].getCurrentValue());
				const float32x2_t state = vld1_f32(&state_[2 * i]);
				vst1_f32(&state_[2 * i - 2], vmla_f32(vmla_f32(state, coeffA, input), coeffB, output));
			}
			return {output[0], output[1]};
		} else if constexpr (sizeof(T) == 8) {
			const float64x2_t input = {inputL, inputR};
			const float64x2_t output = coeffs_[0].getCurrentValue() * input + vld1q_f64(&state_[0]);
			LOOP_UNROLL_FULL()
			for (int i = 1; i < order + 1; ++i) {
				const float64x2_t coeffA = vdupq_n_f64(coeffs_[i].getCurrentValue());
				const float64x2_t coeffB = vdupq_n_f64(coeffs_[order + i].getCurrentValue());
				const float64x2_t state = vld1q_f64(&state_[2 * i]);
				vst1q_f64(&state_[2 * i - 2], vmlaq_f64(vmlaq_f64(state, coeffA, input), coeffB, output));
			}
			return {output[0], output[1]};
		}

		return {T(0), T(0)};
	}
#else
	FORCE_INLINE std::pair<T, T> process_pair(const T inputL, const T inputR) noexcept {
		if constexpr (gradual) {
			if (coeffs_[0].isUpdating()) {
				for (size_t i = 0; i < coeffs_.size(); ++i) coeffs_[i].forceUpdate();
			}
		}
		const T outputL = coeffs_[0].getCurrentValue() * inputL + state_[0];
		const T outputR = coeffs_[0].getCurrentValue() * inputR + state_[1];

		LOOP_UNROLL_FULL()
		for (int i = 1; i < order + 1; ++i) {
			const T coeffA = coeffs_[i].getCurrentValue();
			const T coeffB = coeffs_[order + i].getCurrentValue();
			state_[2 * i - 2] = coeffA * inputL + coeffB * outputL + state_[i << 1 | 0];
			state_[2 * i - 1] = coeffA * inputR + coeffB * outputR + state_[i << 1 | 1];
		}

		return {outputL, outputR};
	}
#endif

	T operator()(T input) noexcept { return process(input); }

	std::pair<T, T> operator()(T inputL, T inputR) noexcept { return process_pair(inputL, inputR); }

	void update(const coeff_array &coeffs) noexcept {
		for (size_t i = 0; i < 2 * order + 1; ++i) { coeffs_[i] = coeffs[i]; }
	}

	void update(coeff_gradient_array &&coeffs) noexcept { coeffs_ = std::move(coeffs); }

	void reset(T initValue = 0) noexcept { std::fill(state_.begin(), state_.end(), initValue); }

	void sync_all() {
		for (size_t i = 0; i < coeffs_.size(); ++i) { coeffs_[i].sync(); }
	}

	[[nodiscard]] const coeff_gradient_array &coefficients() const noexcept { return coeffs_; }
	[[nodiscard]] const state_array &state() const noexcept { return state_; }

private:
	alignas(32) coeff_gradient_array coeffs_;
	alignas(32) state_array state_;
};

template <typename T, uint32_t order = 2, bool gradual = false>
class MultiStateFilters {
public:
	using filter_type = IIR_DirectFormIITransposed<T, order, gradual>;
	using value_type = typename filter_type::value_type;
	using coeff_array = typename filter_type::coeff_array;
	using container_type = std::vector<filter_type, std::allocator<filter_type>>;
	using iterator = typename container_type::iterator;
	using const_iterator = typename container_type::const_iterator;
	using size_type = typename container_type::size_type;
	using filter_coeffs_vector = std::vector<typename filter_type::coeff_array>;

	MultiStateFilters() = default;
	MultiStateFilters(const MultiStateFilters &) = default;
	MultiStateFilters(MultiStateFilters &&) noexcept = default;
	MultiStateFilters &operator=(const MultiStateFilters &) = default;
	MultiStateFilters &operator=(MultiStateFilters &&) noexcept = default;

	template <typename Container>
	constexpr explicit MultiStateFilters(const Container &coeffs) {
		init_filters(coeffs);
	}

	iterator begin() noexcept { return filters_.begin(); }
	iterator end() noexcept { return filters_.end(); }
	const_iterator begin() const noexcept { return filters_.begin(); }
	const_iterator end() const noexcept { return filters_.end(); }
	const_iterator cbegin() const noexcept { return filters_.cbegin(); }
	const_iterator cend() const noexcept { return filters_.cend(); }

	[[nodiscard]] bool empty() const noexcept { return filters_.empty(); }
	[[nodiscard]] size_type size() const noexcept { return filters_.size(); }
	void reserve(size_type new_cap) { filters_.reserve(new_cap); }
	void resize(size_type count) { filters_.resize(count); }
	void clear() noexcept { filters_.clear(); }

	filter_coeffs_vector get_coeffs() const {
		filter_coeffs_vector coeffs;
		coeffs.reserve(filters_.size());
		for (const auto &filter : filters_) {
			auto coeff = filter.coefficients();
			coeffs.push_back({coeff[0], coeff[1], coeff[2], coeff[3], coeff[4]});
		}
		return coeffs;
	}

	template <typename Container>
	void update(const Container &coeffs) {
		if (coeffs.size() != filters_.size()) {
			filters_.resize(coeffs.size());
			std::transform(coeffs.begin(), coeffs.end(), filters_.begin(), [](const auto &coeff) {
				coeff_array tmp;
				// 显式转换
				std::transform(coeff.begin(), coeff.end(), tmp.begin(), [](const auto &val) { return static_cast<T>(val); });
				return filter_type(tmp);
			});
		} else {
			for (size_t i = 0; i < coeffs.size(); ++i) {
				coeff_array tmp;
				// 显式转换
				std::transform(coeffs[i].begin(), coeffs[i].end(), tmp.begin(), [](const auto &val) { return static_cast<T>(val); });
				filters_[i].update(tmp);
			}
		}
	}

	void update(coeff_array coeff, size_type index) {
		if (index < filters_.size() && index >= 0) { filters_[index].update(coeff); }
	}

	void updateAll(const coeff_array &coeff) noexcept {
		for (auto &filter : filters_) { filter.update(coeff); }
	}

	[[nodiscard]] FORCE_INLINE T process(T input) noexcept {
		return std::accumulate(filters_.begin(), filters_.end(), input, [](T value, auto &filter) { return filter.process(value); });
	}

	FORCE_INLINE void processBatch(T *inputs, T *outputs, int size) noexcept {
		for (auto &filter : filters_) { filter.processBatch(inputs, outputs, size); }
		// return std::accumulate(filters_.begin(), filters_.end(), input, [](T value, auto &filter) { return filter.process(value); });
	}

	[[nodiscard]] FORCE_INLINE std::pair<T, T> process_pair(T inputL, T inputR) noexcept {
		auto result = std::make_pair(inputL, inputR);
		// for (auto &filter : filters_) result = filter.process_pair(result.first, result.second);
		for (int i = 0; i < filters_.size(); ++i) { result = filters_[i].process_pair(result.first, result.second); }
		return result;
	}


	void reset_all(T initValue = 0) noexcept {
		std::for_each(filters_.begin(), filters_.end(), [&](auto &filter) { filter.reset(initValue); });
	}

	void sync_all() {
		std::for_each(filters_.begin(), filters_.end(), [&](auto &filter) { filter.sync_all(); });
	}

	T operator()(T input) noexcept { return process(input); }

	std::pair<T, T> operator()(T inputL, T inputR) noexcept { return process_pair(inputL, inputR); }

	[[nodiscard]] const container_type &filters() const noexcept { return filters_; }

private:
	template <typename Container>
	void init_filters(const Container &coeffs) {
		using coeff_value_type = typename std::decay_t<typename Container::value_type>::value_type;

		static_assert(std::is_convertible_v<coeff_value_type, T>, "Coefficient type must be convertible to filter type");

		if constexpr (!std::is_same_v<coeff_value_type, T>) {
			[[deprecated("Type conversion may cause performance loss")]] auto type_conversion_warning = []() {};
			type_conversion_warning();
		}

		filters_.reserve(coeffs.size());
		std::transform(coeffs.begin(), coeffs.end(), std::back_inserter(filters_), [](const auto &coeff) {
			if constexpr (std::is_same_v<coeff_value_type, T>) {
				return filter_type(coeff);
			} else {
				coeff_array tmp;
				// 使用显式转换替代隐式转换
				std::transform(coeff.begin(), coeff.end(), tmp.begin(), [](const auto &val) { return static_cast<T>(val); });
				return filter_type(tmp);
			}
		});
	}

	container_type filters_;
};

template <typename T, uint32_t order = 2, bool gradual = false>
class MultiFiltersSimdWithDelay {
	using arch_t = xsimd::best_arch;
	constexpr static int32_t N = xsimd::batch<T, arch_t>::size;
	using batch_t = xsimd::batch<T, arch_t>;
	constexpr static size_t coeff_size = N * (2 * order + 1);
	constexpr static size_t state_size = 2 * N * (order + 1);

	// 对齐包装：AVX 需 32 字节，AVX-512 需 64 字节，统一用 64 覆盖所有情况
	struct alignas(32) aligned_coeff_array : std::array<T, coeff_size> {
		using std::array<T, coeff_size>::array;
	};
	struct alignas(32) aligned_state_array : std::array<T, state_size> {
		using std::array<T, state_size>::array;
	};

public:
	MultiFiltersSimdWithDelay(std::vector<std::array<T, 2 * order + 1>> coeffs = {}) { update(coeffs); }
	void update(std::vector<std::array<T, 2 * order + 1>> coeffs) {
		while (coeffs.size() % N != 0) coeffs.push_back({1, 0, 0, 0, 0});
		coeffs_.resize(coeffs.size() / N);
		states_.resize(coeffs.size() / N, aligned_state_array{});
		for (size_t i = 0; i < coeffs.size() / N; ++i) {
			for (size_t j = 0; j < 5; ++j) {
				for (size_t k = 0; k < N; ++k) coeffs_[i][j * N + k] = coeffs[i * N + k][j];
			}
		}
	}
	void reset_all(T initValue = 0) noexcept {
		for (auto &state : states_) { std::fill(state.begin(), state.end(), initValue); }
	}
	T operator()(T input) { return process(input); }
	std::pair<T, T> operator()(T inputL, T inputR) noexcept { return process_pair(inputL, inputR); }
	FORCE_INLINE T process(T input) noexcept {
		batch_t in, out;
		for (int j = 0; j < (int)coeffs_.size(); ++j) {
			in = xsimd::insert(xsimd::load_aligned<arch_t>(&states_[j][0]), input, xsimd::index<0>());
			out = xsimd::load_aligned<arch_t>(&coeffs_[j][0]) * in + xsimd::load_aligned<arch_t>(&states_[j][N]);

			for (size_t i = 1; i < order + 1; ++i) {
				const batch_t coeffA = xsimd::load_aligned<arch_t>(&coeffs_[j][i * N]), coeffB = xsimd::load_aligned<arch_t>(&coeffs_[j][(order + i) * N]);
				xsimd::store_aligned<arch_t>(&states_[j][N * i], coeffA * in + coeffB * out + xsimd::load_aligned<arch_t>(&states_[j][N * (i + 1)]));
			}
			xsimd::store_aligned<arch_t>(&states_[j][0], xsimd::rotate_right<1>(out));
			input = out.get(N - 1);
		}
		return out.get(N - 1);
	}

	// ── 批量处理：numSamples 个连续样本，系数一次性加载保持在寄存器中 ──
	void process(const T* input, T* output, size_t numSamples) noexcept {
		if (numSamples == 0) return;
		const T* inPtr = input;
		for (int j = 0; j < (int)coeffs_.size(); ++j) {
			// ═══ 预加载本级全部系数 (5 次 unaligned load，仅此 1 次) ═══
			const batch_t cb0 = xsimd::load_aligned<arch_t>(&coeffs_[j][0]);               // b0
			const batch_t cb1 = xsimd::load_aligned<arch_t>(&coeffs_[j][N]);               // b1
			const batch_t cb2 = xsimd::load_aligned<arch_t>(&coeffs_[j][2 * N]);           // b2
			const batch_t ca1 = xsimd::load_aligned<arch_t>(&coeffs_[j][(order + 1) * N]); // -a1
			const batch_t ca2 = xsimd::load_aligned<arch_t>(&coeffs_[j][(order + 2) * N]); // -a2

			// ═══ 预加载本级全部状态，后续在寄存器中更新 ═══
			batch_t s0  = xsimd::load_aligned<arch_t>(&states_[j][0]);          // d1 (rotate_right 后的 out)
			batch_t sN  = xsimd::load_aligned<arch_t>(&states_[j][N]);          // d2 (加到 b0*in 的延迟项)
			batch_t s2N = xsimd::load_aligned<arch_t>(&states_[j][2 * N]);      // 移位寄存器 1
			batch_t s3N = xsimd::load_aligned<arch_t>(&states_[j][3 * N]);      // 移位寄存器 2 (biquad 为 0)

			// ═══ 逐样本处理 (系数 + 状态均在寄存器中) ═══
			for (size_t m = 0; m < numSamples; ++m) {
				T x = inPtr[m];
				batch_t inV  = xsimd::insert(s0, x, xsimd::index<0>());  // {x, s0[1], s0[2], ...}
				batch_t outV = cb0 * inV + sN;                            // y = b0*x + d2

				// 状态移位链 (等效于原始 i=1..2 循环展开)
				sN  = cb1 * inV + ca1 * outV + s2N;                    // d1_next = b1*x - a1*y + s2N
				s2N = cb2 * inV + ca2 * outV + s3N;                    // d2_next = b2*x - a2*y + s3N
				s0  = xsimd::rotate_right<1>(outV);                    // 准备下一轮 insert

				output[m] = outV.get(N - 1);
			}

			// ═══ 写回状态 ═══
			xsimd::store_aligned<arch_t>(&states_[j][0],       s0);
			xsimd::store_aligned<arch_t>(&states_[j][N],       sN);
			xsimd::store_aligned<arch_t>(&states_[j][2 * N],   s2N);
			// s3N 始终为 0，biquad 无需写回

			inPtr = output; // 本级输出成为下一级输入
		}
	}

	// ── 可变元：process(a, b, c...) → std::array<T, N>，编译器完全展开 ──
	template <typename... Args, typename = std::enable_if_t<(std::is_convertible_v<Args, T> && ...)>>
	FORCE_INLINE auto process(Args... inputs) noexcept {
		constexpr size_t M = sizeof...(Args);
		T in[M] = {static_cast<T>(inputs)...};
		std::array<T, M> out;
		process(in, out.data(), M);
		return out;
	}

	FORCE_INLINE std::pair<T, T> process_pair(T inputL, T inputR) noexcept {
		batch_t inL, inR, outL, outR;
		for (int j = 0; j < (int)coeffs_.size(); ++j) {
			inL = xsimd::insert(xsimd::load_aligned<arch_t>(&states_[j][0]), inputL, xsimd::index<0>());
			inR = xsimd::insert(xsimd::load_aligned<arch_t>(&states_[j][N * (order + 1)]), inputL, xsimd::index<0>());
			outL = xsimd::load_aligned<arch_t>(&coeffs_[j][0]) * inL + xsimd::load_aligned<arch_t>(&states_[j][N]);
			outR = xsimd::load_aligned<arch_t>(&coeffs_[j][0]) * inR + xsimd::load_aligned<arch_t>(&states_[j][N + N * (order + 1)]);

			for (size_t i = 1; i < order + 1; ++i) {
				const batch_t coeffA = xsimd::load_aligned<arch_t>(&coeffs_[j][i * N]), coeffB = xsimd::load_aligned<arch_t>(&coeffs_[j][(order + i) * N]);
				xsimd::store_aligned<arch_t>(&states_[j][N * i], coeffA * inL + coeffB * outL + xsimd::load_aligned<arch_t>(&states_[j][N * (i + 1)]));
				xsimd::store_aligned<arch_t>(&states_[j][N * i + N * (order + 1)], coeffA * inL + coeffB * outL + xsimd::load_aligned<arch_t>(&states_[j][N * (i + 1) + N * (order + 1)]));
			}
			xsimd::store_aligned<arch_t>(&states_[j][0], outL);
			xsimd::store_aligned<arch_t>(&states_[j][N * (order + 1)], outR);
			inputL = outL.get(N - 1), inputR = outR.get(N - 1);
		}
		return {outL.get(N - 1), outR.get(N - 1)};
	}

private:
	std::vector<aligned_coeff_array> coeffs_;
	std::vector<aligned_state_array> states_;
};


template <typename T, uint32_t numAB = 3>
std::vector<T> freqz(const std::vector<T> &b, const std::vector<T> &a, std::vector<T> &v) {
	std::vector<T> response(v.size());
	constexpr T pi = T(3.14159265358979323846);

	// 计算0到pi的频率响应
	for (size_t i = 0; i < v.size(); ++i) {
		std::complex<double> numerator(0, 0);
		std::complex<double> denominator(0, 0);

		// 计算分子
		for (size_t k = 0; k < numAB; ++k) { numerator += (double)b[k] * (std::complex<double>)(std::exp(std::complex<double>(0, -v[i] * pi * k))); }

		// 计算分母
		for (size_t k = 0; k < numAB; ++k) { denominator += (double)a[k] * (std::complex<double>)(std::exp(std::complex<double>(0, -v[i] * pi * k))); }

		response[i] = std::abs(numerator / denominator);
	}
	return response;
}

template <typename T>
std::vector<T> freqzSOS(const std::vector<std::array<T, 5>> &sos, std::vector<T> &v) {
	if (sos.empty()) {
		return std::vector<T>(v.size(), T(1)); // 如果sos为空，返回全1的响应
	}
	auto bode = freqz(std::vector<T>{sos[0][0], sos[0][1], sos[0][2]}, {(T)1, -sos[0][3], -sos[0][4]}, v);
	for (uint32_t j = 1; j < sos.size(); ++j) {
		auto tmp = freqz(std::vector<T>{sos[j][0], sos[j][1], sos[j][2]}, {(T)1, -sos[j][3], -sos[j][4]}, v);
		for (uint32_t k = 0; k < bode.size(); ++k) bode[k] *= tmp[k];
	}

	return bode;
}

template <typename T, uint32_t numAB = 3>
std::vector<T, std::allocator<T>> freqzCached(const std::vector<T> &b, const std::vector<T> &a, const std::complex<double> *cache, size_t points = NeoEqFreqzPoints) {
	std::vector<T> response(points);
	constexpr T pi = T(3.14159265358979323846);

	// 计算0到pi的频率响应
	for (size_t i = 0; i < points; ++i) {
		std::complex<T> numerator(0, 0);
		std::complex<T> denominator(0, 0);

		// 计算分子
		for (size_t k = 0; k < numAB; ++k) { numerator += b[k] * (std::complex<T>)cache[i * numAB + k]; }

		// 计算分母
		for (size_t k = 0; k < numAB; ++k) { denominator += a[k] * (std::complex<T>)cache[i * numAB + k]; }

		response[i] = std::abs(numerator / denominator);
	}
	return response;
}

enum class FilterType : uint32_t {
	None,
	SingleLowpass,
	SingleHighpass,
	SingleTilts,
	BiquadLowPass,
	BiquadHighPass,
	BiquadBandPass,
	BiquadNorch,
	BiquadPeak,
	BesselLowPass,
	BesselHighPass,
	ButterworthLowPass,
	ButterworthHighPass,
	ButterworthBandPass,
	ButterworthBandStop,
	ButterworthLowshelf,
	ButterworthHighshelf,
	ButterworthTiltshelf,
	ButterworthFlatTilt,
	ChebyshevLowPass,
	ChebyshevHighPass
};

static constexpr const char *FilterTypeChars[] = {"None", "SingleLowpass", "SingleHighpass", "SingleTilts", "BiquadLowPass", "BiquadHighPass", "BiquadBandPass", "BiquadNorch", "BiquadPeak",
    "BesselLowPass", "BesselHighPass", "ButterworthLowPass", "ButterworthHighPass", "ButterworthBandPass", "ButterworthBandStop", "ButterworthLowshelf", "ButterworthHighshelf", "ButterworthTiltshelf",
    "ButterworthFlatTilt", "ChebyshevLowPass", "ChebyshevHighPass"};
constexpr uint32_t FilterTypeCount = sizeof(FilterTypeChars) / sizeof(FilterTypeChars[0]);

static constexpr const char *FilterOrderChars[]{
    "6dB/oct", "12dB/oct", "18dB/oct", "24dB/oct", "30dB/oct", "36dB/oct", "42dB/oct", "48dB/oct", "54dB/oct", "60dB/oct", "66dB/oct", "72dB/oct", "78dB/oct", "84dB/oct", "90dB/oct"};
constexpr uint32_t FilterOrderCount = sizeof(FilterOrderChars) / sizeof(FilterOrderChars[0]);

template <typename T, uint32_t num = 32, int32_t hop = 512>
class EqGuiAllInOne {
	constexpr static uint32_t eqUpdatehop = hop;

public:
	EqGuiAllInOne(uint32_t sampleRate) : sampleRate_(sampleRate) { prepare(sampleRate); }

	~EqGuiAllInOne() {
		while (running.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
	}

	void prepare(uint32_t sampleRate) {
		sampleRate_ = sampleRate;
		coeffs_.resize(num);
		originnCoeffs_.resize(num);
		for (int i = 0; i < num; i++) freqs_[i].setSampleRate(sampleRate_ / eqUpdatehop), freqs_[i].setAttackTime(0.005);
		for (int i = 0; i < num; i++) gains_[i].setSampleRate(sampleRate_ / eqUpdatehop), gains_[i].setAttackTime(0.001);
		for (int i = 0; i < num; i++) Qs_[i].setSampleRate(sampleRate_ / eqUpdatehop), Qs_[i].setAttackTime(0.001);
		sideChainBuffers_.resize(num);
		for (int i = 0; i <= num; i++) std::fill(freqzData[i].begin(), freqzData[i].end(), 0);
		auto tmp = log(20000.0f / 20.0f);
		freqzIndex.resize(NeoEqFreqzPoints);
		for (int i = 0; i < NeoEqFreqzPoints; ++i) { freqzIndex[i] = std::exp(log(20.0f) + tmp * (float(i) / float(NeoEqFreqzPoints - 1))) / (sampleRate_ / 2.0f); }
	}

	template <typename S>
	std::vector<std::array<S, 5>> calcCoeffs(enum FilterType type, uint32_t order, S freq, S gain = 0, S Q = 1) {
		std::vector<std::array<S, 5>> coeff;
		freq = std::clamp(freq, S(1e-4), S(0.99f)); // 限制频率范围
		switch (type) {
		case FilterType::None: coeff = std::vector<std::array<S, 5>>(); break;
		case FilterType::SingleLowpass: coeff = std::vector<decltype(biquad::single_lowpass(freq))>(1, biquad::single_lowpass(freq)); break;
		case FilterType::SingleHighpass: coeff = std::vector<decltype(biquad::single_highpass(freq))>(1, biquad::single_highpass(freq)); break;
		case FilterType::SingleTilts:
			{
				const int N = (int)log2(sampleRate_ / 20.0);
				S f = 0.99;
				while (f > 20.0 / sampleRate_) {
					coeff.push_back(ZpkFilter::normalize_section(biquad::single_combine(biquad::single_tilt(f, gain / N), biquad::single_tilt(f / (S)2.0, gain / N)), freq));
					f /= 4.0;
				}
				break;
			}
		case FilterType::BiquadLowPass:
			// if (gain) Q = DB_CO(gain);
			coeff = std::vector<decltype(biquad::biquad_lowpass(freq, Q))>(1, biquad::biquad_lowpass(freq, Q));
			break;
		case FilterType::BiquadHighPass:
			// if (gain) Q = DB_CO(gain);
			coeff = std::vector<decltype(biquad::biquad_highpass(freq, Q))>(1, biquad::biquad_highpass(freq, Q));
			break;
		case FilterType::BiquadBandPass:
			// if (gain) Q = DB_CO(gain);
			coeff = std::vector<decltype(biquad::biquad_bandpass(freq, Q))>(1, biquad::biquad_bandpass(freq, Q));
			break;
		case FilterType::BiquadNorch:
			// if (gain) Q = DB_CO(gain);
			coeff = std::vector<decltype(biquad::biquad_notch(freq, Q))>(1, biquad::biquad_notch(freq, Q));
			break;
		case FilterType::BiquadPeak: coeff = std::vector<decltype(biquad::biquad_peak(freq, gain, Q * 4))>(1, biquad::biquad_peak(freq, gain, Q * 4)); break;
		case FilterType::ButterworthLowshelf:
			coeff = ZpkFilter::to_sos(ZpkFilter::iir_butterworth_lowshelf(freq, 2 * gain, order), (S)1.0);
			// coeff = std::vector<decltype(biquad::biquad_lowshelf_withQ(freq, gain, Q * 0.707))>(1, biquad::biquad_lowshelf_withQ(freq, 2*gain, Q * 0.707));
			break;
		case FilterType::ButterworthHighshelf:
			coeff = ZpkFilter::to_sos(ZpkFilter::iir_butterworth_highshelf(freq, 2 * gain, order));
			// coeff = std::vector<decltype(biquad::biquad_highshelf_withQ(freq, gain, Q * 0.707))>(1, biquad::biquad_highshelf_withQ(freq, 2*gain, Q * 0.707));
			break;
		case FilterType::ButterworthTiltshelf:
			{
				// coeff = ZpkFilter::to_sos(ZpkFilter::iir_butterworth_lowshelf(freq, gain, order), 1.0);
				// auto coeff2 = ZpkFilter::to_sos(ZpkFilter::iir_butterworth_highshelf(freq, -gain, order));
				coeff = std::vector<decltype(biquad::biquad_lowshelf_withQ(freq, gain, Q * (S)0.707))>(order / 2, biquad::biquad_lowshelf_withQ(freq, gain, Q * (S)0.707));
				auto coeff2 = std::vector<decltype(biquad::biquad_highshelf_withQ(freq, -gain, Q * (S)0.707))>(order / 2, biquad::biquad_highshelf_withQ(freq, -gain, Q * (S)0.707));
				coeff.reserve(coeff.size() + coeff2.size());
				coeff.insert(coeff.end(), coeff2.begin(), coeff2.end());
				break;
			}
		case FilterType::ButterworthFlatTilt:
			{
				std::vector<std::array<S, 5>> tmp;
				S freqq = 0.99;
				tmp = ZpkFilter::to_sos(ZpkFilter::iir_butterworth_highshelf(freqq, -gain, 1));
				// coeff = std::vector<decltype(biquad::biquad_highshelf(freq, gain))>(1, biquad::biquad_lowshelf(freqq, -gain));
				while (freqq > 5.0 / 24000.0) {
					auto coeff2 = ZpkFilter::to_sos(ZpkFilter::iir_butterworth_lowshelf(freqq, -gain, 1));
					// auto coeff2 = std::vector<decltype(biquad::biquad_lowshelf(freq, gain))>(1, biquad::biquad_lowshelf(freqq, -gain));
					tmp.reserve(tmp.size() + coeff2.size());
					tmp.insert(tmp.end(), coeff2.begin(), coeff2.end());
					freqq /= 4;
				}

				for (int i = 0; i < tmp.size() - 1; i += 2) { coeff.push_back(biquad::single_combine(tmp[i], tmp[i + 1])); }

				auto f = std::vector<S>({freq});
				auto p = freqzSOS(coeff, f);
				coeff[0][0] /= p[0], coeff[0][1] /= p[0], coeff[0][2] /= p[0];
				break;
			}
		case FilterType::BesselLowPass: coeff = ZpkFilter::to_sos(ZpkFilter::iir_lowpass(ZpkFilter::bessel<S>(order), freq)); break;
		case FilterType::BesselHighPass: coeff = ZpkFilter::to_sos(ZpkFilter::iir_highpass(ZpkFilter::bessel<S>(order), freq), (S)1.0); break;
		case FilterType::ButterworthLowPass: coeff = ZpkFilter::to_sos(ZpkFilter::iir_lowpass(ZpkFilter::butterworth<S>(order), freq)); break;
		case FilterType::ButterworthHighPass: coeff = ZpkFilter::to_sos(ZpkFilter::iir_highpass(ZpkFilter::butterworth<S>(order), freq), (S)1.0); break;
		case FilterType::ButterworthBandPass:
			{
				auto lowFreq = std::max((S)0.0, freq / (1 + Q)), highFreq = std::min((S)0.99, freq * (1 + Q));
				coeff = ZpkFilter::to_sos(ZpkFilter::iir_bandpass(ZpkFilter::butterworth<S>(order), lowFreq, highFreq), (S)sqrt(lowFreq * highFreq));
				break;
			}
		case FilterType::ButterworthBandStop:
			{
				auto lowFreq = std::max<S>((T)0.0, freq * Q / (1 + Q)), highFreq = std::min<S>((T)0.9, (2 * freq + freq * Q) / (1 + Q));
				coeff = ZpkFilter::to_sos(ZpkFilter::iir_bandstop(ZpkFilter::butterworth<S>(order), lowFreq, highFreq));
				break;
			}
		case FilterType::ChebyshevLowPass:
			if (abs(gain) < 1e-5) { coeff = std::vector<std::array<S, 5>>((order + 1) / 2, {(S)1.0f, (S)0.0f, (S)0.0f, (S)0.0f, (S)0.0f}); }
			else { coeff = ZpkFilter::to_sos(ZpkFilter::iir_lowpass(ZpkFilter::chebyshev2(order, std::abs(gain)), freq), (S)0.0); }
			break;
		case FilterType::ChebyshevHighPass:
			if (abs(gain) < 1e-5) { coeff = std::vector<std::array<S, 5>>((order + 1) / 2, {(S)1.0f, (S)0.0f, (S)0.0f, (S)0.0f, (S)0.0f}); }
			else { coeff = ZpkFilter::to_sos(ZpkFilter::iir_highpass(ZpkFilter::chebyshev2(order, std::abs(gain)), freq), S(1.0)); }
			break;
		default: break;
		}

		return coeff;
	}

	void updateEq(uint32_t index) {
		if (index >= filtersL_.size() || !inUse[index]) return;
		T freq = freqs_[index] * 2 / sampleRate_;
		T gain = gains_[index];
		T Q = Qs_[index];
		changed_[index] = freqs_[index].getState().flagUpdate || gains_[index].getState().flagUpdate || Qs_[index].getState().flagUpdate;
		auto coeff = calcCoeffs(types_[index], orders_[index], freq, gain, Q);
		filtersL_[index].update(coeff);
		coeffs_[index] = coeff;
		originnCoeffs_[index] = calcCoeffs(types_[index], orders_[index], (double)freqs_[index].getTargetValue() * 2 / sampleRate_, (double)gains_[index].getTargetValue(),
		    (double)Qs_[index].getTargetValue());
	}

	void updateTargetEq(uint32_t index) {
		changed_[index] = 1;
		auto coeff = calcCoeffs(types_[index], orders_[index], (double)freqs_[index].getTargetValue() * 2 / sampleRate_, (double)gains_[index].getTargetValue(), (double)Qs_[index].getTargetValue());
		originnCoeffs_[index] = coeff;
		auto data = freqzSOS(originnCoeffs_[index], freqzIndex);
		std::transform(data.begin(), data.end(), freqzData[index].begin(), [](auto v) { return 20 * log10f(v + 1e-20f); });
	}

	template <typename S>
	void processInterleave(S *restrict inputs, S *restrict outputs, size_t size) {
		// auto start = std::chrono::high_resolution_clock::now();
		running = 1;
		// for (int i = 0; i < size << 1; i++) { outputs[i] = inputs[i]; }
		if (inputs != outputs) memcpy(outputs, inputs, sizeof(S) * size * 2);
		if (updateFlag <= 0) {
			for (int i = 0; i < num; ++i) {
				if (inUse[i] && changed_[i]) updateEq(i);
				if (changed_[i]) updateFlag = eqUpdatehop;
			}
		}
		updateFlag -= size;
		for (int i = 0; i < num; ++i) {
			if (inUse[i]) {
				for (int j = 0; j < size; ++j) {
					auto pair = filtersL_[i].process_pair(outputs[j * 2], outputs[j * 2 + 1]);
					outputs[j * 2] = pair.first, outputs[j * 2 + 1] = pair.second;
					// 这里std::pair的优化不太好，是用两个寄存器而不是一个neon寄存器存的；但是由于指令重排的存在，优化这部分代码意义不大，而且这样代码更好看
				}
			}
		}
		running = 0;
		// auto end = std::chrono::high_resolution_clock::now();
		// std::cout << "Neo EQ Process Time: " << std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count() << " ns" << std::endl;
	}
	template <typename S>
	void processMono(S *restrict inputs, S *restrict outputs, size_t size) {
		running = 1;
		for (int i = 0; i < size; i++) { outputs[i] = inputs[i]; }
		if (updateFlag <= 0) {
			for (int i = 0; i < num; ++i) {
				if (inUse[i]) updateEq(i);
				if (changed_[i]) updateFlag = eqUpdatehop;
			}
		}
		updateFlag -= size;
		for (int i = 0; i < num; ++i) {
			if (inUse[i]) {
				for (int j = 0; j < size; ++j) { outputs[j * 2] = filtersL_[i].process(outputs[j]); }
			}
		}
		running = 0;
	}
	template <typename S>
	void processStereo(S *restrict inputsL, S *restrict outputsL, S *restrict inputsR, S *restrict outputsR, size_t size) {
		running = 1;
		for (int i = 0; i < size; i++) {
			outputsL[i] = inputsL[i];
			outputsR[i] = inputsR[i];
		}
		if (updateFlag <= 0) {
			for (int i = 0; i < num; ++i) {
				if (inUse[i]) updateEq(i);
				if (changed_[i]) updateFlag = eqUpdatehop;
			}
		}
		updateFlag -= size;
		for (int i = 0; i < num; ++i) {
			if (inUse[i]) {
				for (int j = 0; j < size; ++j) {
					auto pair = filtersL_[i].process_pair(outputsL[j], outputsR[j]);
					outputsL[j] = pair.first, outputsR[j] = pair.second;
				}
			}
		}
		running = 0;
	}

	auto &getSampleRate() { return sampleRate_; }
	auto &getCoeffs() { return coeffs_; }
	auto &getInUse() { return inUse; }
	float *getFreqzData(int32_t eqIndex) {
		if (eqIndex < 0 || eqIndex >= num) {
			std::fill(freqzData[num].begin(), freqzData[num].end(), T(0));
			for (int i = 0; i < num; ++i) {
				for (int j = 0; j < NeoEqFreqzPoints; ++j) { freqzData[num][j] += freqzData[i][j]; }
			}
			return freqzData[num].data();
		} else {
			return freqzData[eqIndex].data();
		}
	}

public:
	float sampleRate_ = 48000;
	constexpr static int32_t size = num;
	std::array<MultiStateFilters<T, 2, true>, num> filtersL_;
	std::vector<std::vector<std::array<T, 5>>> coeffs_;
	std::vector<std::vector<std::array<double, 5>>> originnCoeffs_;
	std::array<uint32_t, num> inUse; // 暂定为1是普通滤波器，2是动态EQ，0是空闲
	std::array<uint32_t, num> bypassed_;
	std::array<FilterType, num> types_;
	std::array<uint32_t, num> orders_;
	std::array<GradualLimit<T>, num> freqs_;
	std::array<GradualLimit<T>, num> gains_;
	std::array<GradualLimit<T>, num> Qs_;
	std::array<uint32_t, num> changed_;
	std::array<std::array<float, NeoEqFreqzPoints>, num + 1> freqzData;
	std::vector<double> freqzIndex;
	int updateFlag = 0;
	std::atomic<int> running{0};

private:
	CircularBuffer<T> buffer_;
	std::vector<CircularBuffer<T>> sideChainBuffers_;
};

#endif
