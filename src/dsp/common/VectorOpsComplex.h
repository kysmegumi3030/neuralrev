#ifndef VECTOR_OPS_COMPLEX_H
#define VECTOR_OPS_COMPLEX_H

#include "VectorOps.h"

template <typename T> inline void c_magphase(T *mag, T *phase, T real, T imag) {
	*mag = sqrt(real * real + imag * imag);
	*phase = atan2(imag, real);
}

#ifdef USE_APPROXIMATE_ATAN2
// NB arguments in opposite order from usual for atan2f
extern float approximate_atan2f(float real, float imag);
template <> inline void c_magphase(float *mag, float *phase, float real, float imag) {
	float atan = approximate_atan2f(real, imag);
	*phase = atan;
	*mag = sqrtf(real * real + imag * imag);
}
#else
template <> inline void c_magphase(float *mag, float *phase, float real, float imag) {
	*mag = sqrtf(real * real + imag * imag);
	*phase = atan2f(imag, real);
}
#endif

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>> // S source, T target
void v_polar_to_cartesian(T *const restrict real, T *const restrict imag, const T *const restrict mag, const T *const restrict phase, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) {
			real[i] = cos(phase[i]);
			imag[i] = sin(phase[i]);
		}
		v_multiplyy(real, mag, count);
		v_multiplyy(imag, mag, count);
#elif defined(USE_XSIMD)
		int i = 0;
		for (; i <= count - int(xsimd::batch<T, xsimd::best_arch>::size); i += xsimd::batch<T, xsimd::best_arch>::size) {
			auto phase_v = xsimd::load_aligned<xsimd::best_arch>(phase + i);
			auto magg = xsimd::load_aligned<xsimd::best_arch>(mag + i);
			auto [sins, coss] = xsimd::sincos(phase_v); // 一次调用同时得到 sin/cos，比两次调用快 ~25%
			(coss * magg).store_aligned(real + i);
			(sins * magg).store_aligned(imag + i);
		}
		for (; i < count; ++i) {
			real[i] = cos(phase[i]) * mag[i];
			imag[i] = sin(phase[i]) * mag[i];
		}
#else
		mipp::Reg<T> tmp;
		int i = 0;
		for (; i <= count - mipp::N<T>(); i += mipp::N<T>()) {
			tmp = mipp::load<T>(phase + i);
			mipp::store<T>(real + i, fast_cos(tmp));
			mipp::store<T>(imag + i, fast_sin(tmp));
		}
		for (; i < count; ++i) {
			real[i] = cos(phase[i]);
			imag[i] = sin(phase[i]);
		}
		v_multiply(real, mag, count);
		v_multiply(imag, mag, count);
#endif
	} else {
		for (int i = 0; i < count; ++i) {
			real[i] = cos(phase[i]);
			imag[i] = sin(phase[i]);
		}
	}
}

template <typename T> void v_polar_interleaved_to_cartesian_inplace(T *const restrict srcdst, const int count) {
	T real, imag;
	for (int i = 0; i < count * 2; i += 2) {
		c_phasor(&real, &imag, srcdst[i + 1]);
		real *= srcdst[i];
		imag *= srcdst[i];
		srcdst[i] = real;
		srcdst[i + 1] = imag;
	}
}

template <typename S, typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>> // S source, T target
void v_polar_to_cartesian_interleaved(T *const restrict dst, const S *const restrict mag, const S *const restrict phase, const int count) {
	// T real, imag;
	// for (int i = 0; i < count; ++i) {
	//     c_phasor<T>(&real, &imag, phase[i]);
	//     real *= mag[i];
	//     imag *= mag[i];
	//     dst[i * 2] = real;
	//     dst[i * 2 + 1] = imag;
	// }
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) {
			dst[i * 2] = mag[i] * cos(phase[i]);
			dst[i * 2 + 1] = mag[i] * sin(phase[i]);
		}
#elif defined(USE_XSIMD)
		xsimd::batch<T, xsimd::best_arch> tmp;
		int i = 0;
		for (; i < count - int(xsimd::batch<T, xsimd::best_arch>::size); i += xsimd::batch<T, xsimd::best_arch>::size) {
			tmp = xsimd::load_aligned<xsimd::best_arch>(phase + i);
			auto magg = xsimd::load_aligned<xsimd::best_arch>(mag + i);
			auto coss = xsimd::cos(tmp) * magg;
			auto sins = xsimd::sin(tmp) * magg;
			xsimd::zip_hi(coss, sins).store_unaligned(dst + i * 2);
			xsimd::zip_lo(coss, sins).store_unaligned(dst + i * 2 + xsimd::batch<T, xsimd::best_arch>::size);
		}
		for (; i < count; ++i) {
			dst[i * 2] = mag[i] * cos(phase[i]);
			dst[i * 2 + 1] = mag[i] * sin(phase[i]);
		}
#elif defined(USE_MIPP)
		mipp::Reg<T> tmp;
		for (int i = 0; i < count; i += mipp::N<T>()) {
			tmp = mipp::load<T>(phase + i);
			auto magg = mipp::load<T>(mag + i);
			mipp::store<T>(dst + i * 2, mipp::cos(tmp) * magg);
			mipp::store<T>(dst + i * 2 + 1, mipp::sin(tmp) * magg);
		}
#endif
	} else {
		for (int i = 0; i < count; ++i) {
			dst[i * 2] = mag[i] * cos(phase[i]);
			dst[i * 2 + 1] = mag[i] * sin(phase[i]);
		}
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>> // S source, T target
void v_cartesian_to_polar(T *const restrict mag, T *const restrict phase, const T *const restrict real, const T *const restrict imag, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]);
			phase[i] = atan2(imag[i], real[i]);
		}
#elif defined(USE_XSIMD)
		int i = 0;
		const xsimd::batch<T, xsimd::best_arch> vZero(T(0));
		for (i = 0; i <= count - xsimd::batch<T, xsimd::best_arch>::size; i += xsimd::batch<T, xsimd::best_arch>::size) {
			auto a = xsimd::load_aligned<xsimd::best_arch>(real + i);
			auto b = xsimd::load_aligned<xsimd::best_arch>(imag + i);
			auto sq = xsimd::fma(a, a, b * b);
			auto magg = xsimd::sqrt(sq);
			auto phasee = xsimd::atan2(b, a);
			phasee = xsimd::select(xsimd::eq(sq, vZero), vZero, phasee);
			magg.store_aligned(mag + i);
			phasee.store_aligned(phase + i);
		}
		for (; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]);
			phase[i] = atan2(imag[i], real[i]);
		}
// #else
#endif
	} else {
		for (int i = 0; i < count; ++i) {
			// mag[i] = Sleef_sqrt(double(real[i] * real[i] + imag[i] * imag[i]));
			// phase[i] = Sleef_atan2d1_u10(imag[i], real[i]);
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]);
			phase[i] = atan2(imag[i], real[i]);
		}
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>> // S source, T target
void v_cartesian_to_polar_mul(T *const restrict mag, T *const restrict phase, const T *const restrict real, const T *const restrict imag, const T mulConst, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]) * mulConst;
			phase[i] = atan2(imag[i], real[i]);
		}
#elif defined(USE_XSIMD)
		int i = 0;
		const xsimd::batch<T, xsimd::best_arch> vMul(mulConst), vZero(T(0));
		for (i = 0; i <= count - xsimd::batch<T, xsimd::best_arch>::size; i += xsimd::batch<T, xsimd::best_arch>::size) {
			auto a = xsimd::load_aligned<xsimd::best_arch>(real + i);
			auto b = xsimd::load_aligned<xsimd::best_arch>(imag + i);
			auto sq = xsimd::fma(a, a, b * b); // FMA: 消除 a*a+b*b 的中间舍入
			auto magg = xsimd::sqrt(sq) * vMul;
			auto phasee = xsimd::atan2(b, a);
			phasee = xsimd::select(xsimd::eq(sq, vZero), vZero, phasee); // NaN check 前移到 sq，不依赖 magg 乘法链
			magg.store_aligned(mag + i);
			phasee.store_aligned(phase + i);
		}
		for (; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]) * mulConst;
			phase[i] = atan2(imag[i], real[i]);
		}
// #else
#endif
	} else {
		for (int i = 0; i < count; ++i) {
			// mag[i] = Sleef_sqrt(double(real[i] * real[i] + imag[i] * imag[i]));
			// phase[i] = Sleef_atan2d1_u10(imag[i], real[i]);
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]) * mulConst;
			phase[i] = atan2(imag[i], real[i]);
		}
	}
}

template <typename S, typename T> // S source, T target
void v_cartesian_interleaved_to_polar(T *const restrict mag, T *const restrict phase, const S *const restrict src, const int count) {
	for (int i = 0; i < count; ++i) {
		c_magphase<T>(mag + i, phase + i, src[i * 2], src[i * 2 + 1]);
	}
}

template <typename T> void v_cartesian_to_polar_interleaved_inplace(T *const restrict srcdst, const int count) {
	T mag, phase;
	for (int i = 0; i < count * 2; i += 2) {
		c_magphase(&mag, &phase, srcdst[i], srcdst[i + 1]);
		srcdst[i] = mag;
		srcdst[i + 1] = phase;
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>> // S source, T target
void v_cartesian_to_magnitudes(T *const restrict mag, const T *const restrict real, const T *const restrict imag, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]);
		}
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T, xsimd::best_arch>::size; i += xsimd::batch<T, xsimd::best_arch>::size) {
			auto a = xsimd::load_aligned<xsimd::best_arch>(real + i);
			auto b = xsimd::load_aligned<xsimd::best_arch>(imag + i);
			xsimd::sqrt(a * a + b * b).store_unaligned(mag + i);
		}
		for (; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]);
		}

#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - mipp::N<T>(); i += mipp::N<T>()) {
			mipp::Reg<T> a, b;
			a.load(real + i), b.load(imag + i);
			mipp::Reg<T> value = mipp::sqrt(a * a + b * b);
			value.store(mag + i);
		}
		for (; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]);
		}
#endif
	} else {
		for (int i = 0; i < count; ++i) {
			mag[i] = sqrt(real[i] * real[i] + imag[i] * imag[i]);
		}
	}
}

template <typename S, typename T> // S source, T target
void v_cartesian_interleaved_to_magnitudes(T *const restrict mag, const S *const restrict src, const int count) {
	for (int i = 0; i < count; ++i) {
		mag[i] = T(sqrt(src[i * 2] * src[i * 2] + src[i * 2 + 1] * src[i * 2 + 1]));
	}
}

#endif
