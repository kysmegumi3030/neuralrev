#ifndef VECTOR_OPS_H
#define VECTOR_OPS_H
#include "NeoGlobal.hpp"

#define USE_XSIMD

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_zero(T *const restrict ptr, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		const T value = T(0);
		for (int i = 0; i < count; ++i) { ptr[i] = value; }
#elif defined(USE_XSIMD)
		xsimd::batch<T> value(0);
		int i = 0;
		for (; i < count - int(xsimd::batch<T>::size); i += xsimd::batch<T>::size) { value.store_unaligned(ptr + i); }
		for (; i < count; ++i) { ptr[i] = 0; }
#elif defined(USE_MIPP)
		mipp::Reg<T> value(T(0));
		for (int i = 0; i < count; i += mipp::N<T>()) { value.store(ptr + i); }
#endif
	}
	else { memset(ptr, 0, count * sizeof(T)); }
}

template <typename T, bool simd = false>
inline void v_set(T *const restrict ptr, const T value, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		const T tmp = T(0);
		for (int i = 0; i < count; ++i) { ptr[i] = tmp; }
#elif defined(USE_XSIMD)
		xsimd::batch<T> tmp(value);
		int i = 0;
		for (; i < count - int(xsimd::batch<T>::size); i += xsimd::batch<T>::size) { tmp.store_unaligned(ptr + i); }
		for (; i < count; ++i) { ptr[i] = 0; }
#elif defined(USE_MIPP)
		mipp::Reg<T> tmp(T(0));
		for (int i = 0; i < count; i += mipp::N<T>()) { tmp.store(ptr + i); }
#endif
	}
	else { memset(ptr, 0, count * sizeof(T)); }
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
void v_copy(T *const restrict dst, const T *const restrict src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = src[i]; }
#elif defined(USE_XSIMD)
		if constexpr (std::is_enum<T>::value) {
			for (int i = 0; i < count; ++i) { dst[i] = src[i]; }
		}
		else {
			int i = 0;
			for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
				auto a = xsimd::load_aligned(src + i);
				a.store_aligned(dst + i);
			}
			for (; i < count; ++i) { dst[i] = src[i]; }
		}
#elif defined(USE_MIPP)
		if constexpr (std::is_enum<T>::value) {
			for (int i = 0; i < count; ++i) { dst[i] = src[i]; }
		}
		else {
			int i = 0;
			for (; i <= count - (int)mipp::N<T>(); i += mipp::N<T>()) {
				mipp::Reg<T> value;
				value.load(src + i);
				value.store(dst + i);
			}
			for (; i < count; ++i) { dst[i] = src[i]; }
		}

#endif
	}
	else { memcpy(dst, src, count * sizeof(T)); }
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline T v_absMax(const T *const restrict src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		T maxVal = 0;
		for (int i = 0; i < count; ++i) { maxVal = std::max(maxVal, std::abs(src[i])); }
		return maxVal;
#elif defined(USE_XSIMD)
		T maxVal = 0;
		int i = 0;
		auto maxBatch = xsimd::load_unaligned(src);
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src + i);
			maxBatch = xsimd::max(maxBatch, xsimd::abs(a));
		}
		maxVal = xsimd::reduce_max(maxBatch);
		for (; i < count; ++i) { maxVal = std::max(maxVal, std::abs(src[i])); }
		return maxVal;
#endif
	}
	else {
		T maxVal = 0;
		for (int i = 0; i < count; ++i) { maxVal = std::max(maxVal, std::abs(src[i])); }
		return maxVal;
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_add(T *const restrict dst, const T src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] += src; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(dst + i);
			(a + src).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b;
			a.load(dst + i);
			(a + src).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] += src; }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_add(T *const restrict dst, const T *const restrict src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] += src[i]; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src + i);
			auto b = xsimd::load_unaligned(dst + i);
			(a + b).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src[i]; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b;
			a.load(src + i);
			b.load(dst + i);
			(a + b).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] += src[i]; }
	}
}

template <typename T, typename G, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_add_with_gain(T *const restrict dst, const T *const restrict src, const G gain, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] += src[i] * gain; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src + i);
			auto b = xsimd::load_unaligned(dst + i);
			(a * (T)gain + b).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src[i] * gain; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b;
			a.load(src + i);
			b.load(dst + i);
			(a * (T)gain + b).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src[i] * gain; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] += src[i] * gain; }
	}
}

template <typename T, typename G, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_multiplyConst(T *const restrict dst, const G gain, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] *= gain; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(dst + i);
			(a * (T)gain).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] *= gain; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a;
			a.load(dst + i);
			(a * (T)gain).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] *= gain; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] *= gain; }
	}
}

template <typename T, typename S, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_multiply(T *const restrict dst, const S *const restrict src, const S gain, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = src[i] * gain; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src + i);
			(a * gain).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] = src[i] * gain; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a;
			a.load(src + i);
			(a * gain).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = src[i] * gain; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = src[i] * gain; }
	}
}

template <typename T, typename S, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_multiplyy(T *const restrict dst, const S *const restrict src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] *= src[i]; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_aligned(dst + i);
			auto b = xsimd::load_aligned(src + i);
			(a * b).store_aligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] *= src[i]; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b;
			a.load(dst + i);
			b.load(src + i);
			(a * b).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] *= src[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] *= src[i]; }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_multiply(T *const restrict dst, const T *const restrict src1, const T *const restrict src2, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = src1[i] * src2[i]; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src1 + i);
			auto b = xsimd::load_unaligned(src2 + i);
			(a * b).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] = src1[i] * src2[i]; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b;
			a.load(src1 + i);
			b.load(src2 + i);
			(a * b).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = src1[i] * src2[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = src1[i] * src2[i]; }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_divide(T *const restrict dst, const T *const restrict src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] /= src[i]; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(dst + i);
			auto b = xsimd::load_unaligned(src + i);
			(a / b).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] /= src[i]; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b;
			a.load(dst + i);
			b.load(src + i);
			(a / b).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] /= src[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] /= src[i]; }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_multiply_and_add(T *const restrict dst, const T *const restrict src1, const T *const restrict src2, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] += src1[i] * src2[i]; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_aligned(src1 + i);
			auto b = xsimd::load_aligned(src2 + i);
			auto c = xsimd::load_aligned(dst + i);
			(a * b + c).store_aligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src1[i] * src2[i]; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b, c;
			a.load(src1 + i);
			b.load(src2 + i);
			c.load(dst + i);
			(a * b + c).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] += src1[i] * src2[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] += src1[i] * src2[i]; }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline T v_multiply_and_sum(const T *const restrict src1, const T *const restrict src2, const int count) {
	T result = T();
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { result += src1[i] * src2[i]; }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src1 + i);
			auto b = xsimd::load_unaligned(src2 + i);
			result += xsimd::reduce_add(a * b);
		}
		for (; i < count; ++i) { result += src1[i] * src2[i]; }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a, b, c;
			a.load(src1 + i);
			b.load(src2 + i);
			c.load(dst + i);
			result += mipp::sum(a * b);
		}
		for (; i < count; ++i) { result += src1[i] * src2[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { result += src1[i] * src2[i]; }
	}
	return result;
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_log(T *const restrict dst, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = log(dst[i]); }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(dst + i);
			xsimd::log(a).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] = log(dst[i]); }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a;
			a.load(src + i);
			mipp::log(a).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = log(dst[i]); }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = log(dst[i]); }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_exp(T *const restrict dst, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = exp(dst[i]); }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(dst + i);
			xsimd::exp(a).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] = exp(dst[i]); }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a;
			a.load(src + i);
			mipp::exp(a).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = exp(dst[i]); }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = exp(dst[i]); }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_sqrt(T *const restrict dst, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = sqrt(dst[i]); }
#elif defined(USE_XSIMD)
		int i = 0;
		for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(dst + i);
			xsimd::sqrt(a).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] = sqrt(dst[i]); }
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			mipp::Reg<T> a;
			a.load(src + i);
			mipp::sqrt(a).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = sqrt(dst[i]); }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = sqrt(dst[i]); }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_square(T *const restrict dst, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = dst[i] * dst[i]; }
#elif defined(USE_XSIMD)
		if constexpr (std::is_enum<T>::value) {
			for (int i = 0; i < count; ++i) { dst[i] = dst[i] * dst[i]; }
		}
		else {
			int i = 0;
			for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
				auto a = xsimd::load_unaligned(dst + i);
				(a * a).store_unaligned(dst + i);
			}
			for (; i < count; ++i) { dst[i] = dst[i] * dst[i]; }
		}
#elif defined(USE_MIPP)
		int i = 0;
		for (; i <= count - mipp::N<T>(); i += mipp::N<T>()) {
			mipp::Reg<T> value;
			value.load(dst + i);
			(value * value).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = dst[i] * dst[i]; }
#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = dst[i] * dst[i]; }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_interleave(T *const restrict dst, const T *const restrict *const restrict src, const int channels, const int count) {
	switch (channels) {
	case 2:
#if defined(USE_XSIMD)
		if constexpr (simd) {
			// zip_lo/zip_hi: 每次处理 bsz 个样本，输出 2*bsz 个交错样本
			// 比逐元素写入减少 store 次数约 50%
			constexpr int bsz = xsimd::batch<T>::size;
			int i = 0;
			for (; i <= count - bsz; i += bsz) {
				auto a = xsimd::load_aligned(src[0] + i);
				auto b = xsimd::load_aligned(src[1] + i);
				xsimd::zip_lo(a, b).store_unaligned(dst + i * 2);
				xsimd::zip_hi(a, b).store_unaligned(dst + i * 2 + bsz);
			}
			for (; i < count; ++i) { dst[i * 2] = src[0][i]; dst[i * 2 + 1] = src[1][i]; }
			return;
		}
#endif
		for (int i = 0; i < count; ++i) { dst[i * 2] = src[0][i]; dst[i * 2 + 1] = src[1][i]; }
		return;
	case 1: memcpy(dst, src[0], count * sizeof(T)); return;
	default:
		for (int i = 0, idx = 0; i < count; ++i)
			for (int j = 0; j < channels; ++j) dst[idx++] = src[j][i];
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline void v_deinterleave(T *const restrict *const restrict dst, const T *const restrict src, const int channels, const int count) {
	switch (channels) {
	case 2:
		for (int i = 0; i < count; ++i) { dst[0][i] = src[i * 2]; dst[1][i] = src[i * 2 + 1]; }
		break;
	case 1: memcpy(dst[0], src, count * sizeof(T)); break;
	default:
		for (int i = 0, idx = 0; i < count; ++i)
			for (int j = 0; j < channels; ++j) dst[j][i] = src[idx++];
		break;
	}
}

template <typename T>
inline void v_fftshift(T *const restrict ptr, const int count) {
	const int hs = count / 2;
	for (int i = 0; i < hs; ++i) {
		// T t = ptr[i];
		// ptr[i] = ptr[i + hs];
		// ptr[i + hs] = t;
		std::swap(ptr[i], ptr[i + hs]);
	}
}

template <typename T>
inline T v_mean(const T *const restrict ptr, const int count) {
	T t = T(0);
	int i = 0;
	for (; i < count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
		xsimd::batch<T> b = xsimd::load_unaligned(ptr + i);
		t += xsimd::reduce_add(b);
	}
	for (; i < count; ++i) { t += ptr[i]; }
	t /= T(count);
	return t;
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
void v_DB2CO(T *const restrict dst, const T *const restrict src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = DB_CO(src[i]); }
#elif defined(USE_XSIMD)

		int i = 0;
		for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src + i);
			xsimd::exp(log(10) * a * 0.05).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] = DB_CO(src[i]); }

#elif defined(USE_MIPP)

		int i = 0;
		for (; i <= count - (int)mipp::N<T>(); i += mipp::N<T>()) {
			mipp::Reg<T> value;
			value.load(src + i);
			mipp::exp(log(10.0) * value * 0.05).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = DB_CO(src[i]); }

#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = DB_CO(src[i]); }
	}
}

template <typename T, bool simd = true, typename = std::enable_if_t<std::is_fundamental<T>::value>>
void v_CO2DB(T *const restrict dst, const T *const restrict src, const int count) {
	if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
		for (int i = 0; i < count; ++i) { dst[i] = CO_DB(src[i]); }
#elif defined(USE_XSIMD)

		int i = 0;
		for (i = 0; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
			auto a = xsimd::load_unaligned(src + i);
			(xsimd::log10(a) * 20).store_unaligned(dst + i);
		}
		for (; i < count; ++i) { dst[i] = CO_DB(src[i]); }

#elif defined(USE_MIPP)

		int i = 0;
		for (; i <= count - (int)mipp::N<T>(); i += mipp::N<T>()) {
			mipp::Reg<T> value;
			value.load(src + i);
			mipp::exp(log(10.0) * value * 0.05).store(dst + i);
		}
		for (; i < count; ++i) { dst[i] = CO_DB(src[i]); }

#endif
	}
	else {
		for (int i = 0; i < count; ++i) { dst[i] = CO_DB(src[i]); }
	}
}

template <typename T, bool simd = false, typename = std::enable_if_t<std::is_fundamental<T>::value>>
inline T v_square_sum(const T *const restrict src, const int count) {
    T sum = 0;
    if constexpr (simd) {
#if defined(_DEBUG) || (!defined(USE_XSIMD) && !defined(USE_MIPP))
        for (int i = 0; i < count; ++i) {
            sum += src[i] * src[i];
        }
#elif defined(USE_XSIMD)
        int i = 0;
        for (i = 0; i <= count - xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
            auto a = xsimd::load_unaligned(src + i);
            sum += xsimd::reduce_add(a * a);
        }
        for (; i < count; ++i) {
            sum += src[i] * src[i];
        }
#elif defined(USE_MIPP)
        int i = 0;
        for (; i <= count - (int)xsimd::batch<T>::size; i += xsimd::batch<T>::size) {
            mipp::Reg<T> a;
            a.load(src + i);
            sum += mipp::sum(a * a);
        }
        for (; i < count; ++i) {
            sum += src[i] * src[i];
        }
#endif
    } else {
        for (int i = 0; i < count; ++i) {
            sum += src[i] * src[i];
        }
    }
    return sum;
}

#endif