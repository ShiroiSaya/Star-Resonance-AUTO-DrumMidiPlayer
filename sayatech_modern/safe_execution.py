"""
统一异常处理系统
提供安全的函数执行包装和错误处理
"""
from __future__ import annotations

import functools
import traceback
from typing import Any, Callable, Optional, Type, TypeVar, Union
from contextlib import contextmanager

from .crash_logging import append_runtime_log

T = TypeVar('T')

class SafeExecutionResult:
    """安全执行结果封装"""

    def __init__(
        self,
        success: bool,
        value: Optional[T] = None,
        error: Optional[Exception] = None,
        error_type: Optional[str] = None
    ):
        self.success = success
        self.value = value
        self.error = error
        self.error_type = error_type or (type(error).__name__ if error else None)

    def __bool__(self) -> bool:
        return self.success

    def unwrap(self) -> T:
        """获取值，如果失败则抛出异常"""
        if not self.success:
            raise self.error or RuntimeError("安全执行失败")
        return self.value

    def unwrap_or(self, default: T) -> T:
        """获取值，失败时返回默认值"""
        return self.value if self.success else default


def safe_call(
    func: Callable[..., T],
    default: Optional[T] = None,
    log_errors: bool = True,
    reraise: bool = False,
    expected_errors: tuple[Type[Exception], ...] = (Exception,),
    context: str = ""
) -> Union[T, SafeExecutionResult[T]]:
    """
    安全执行函数，自动处理异常

    Args:
        func: 要执行的函数
        default: 失败时的默认返回值
        log_errors: 是否记录错误日志
        reraise: 是否重新抛出异常
        expected_errors: 期望处理的异常类型
        context: 上下文信息，用于日志

    Returns:
        函数返回值或 SafeExecutionResult
    """
    try:
        result = func()
        return result
    except expected_errors as e:
        error_msg = f"{context} {func.__name__ if hasattr(func, '__name__') else str(func)} 失败"
        if context:
            error_msg = f"{context}: {error_msg}"

        if log_errors:
            append_runtime_log(
                f"{error_msg}: {type(e).__name__}: {e}",
                debug=True
            )

        if reraise:
            raise

        return default


def safe_method(
    log_errors: bool = True,
    reraise: bool = False,
    default_return: Any = None,
    expected_errors: tuple[Type[Exception], ...] = (Exception,)
):
    """
    方法装饰器，提供安全执行包装

    Args:
        log_errors: 是否记录错误
        reraise: 是否重新抛出异常
        default_return: 失败时的默认返回值
        expected_errors: 期望处理的异常类型
    """
    def decorator(func: Callable[..., T]) -> Callable[..., Union[T, SafeExecutionResult[T]]]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Union[T, SafeExecutionResult[T]]:
            try:
                return func(*args, **kwargs)
            except expected_errors as e:
                if log_errors:
                    append_runtime_log(
                        f"{func.__qualname__} 异常: {type(e).__name__}: {e}",
                        debug=True
                    )
                if reraise:
                    raise
                return default_return
        return wrapper
    return decorator


@contextmanager
def safe_context(
    log_errors: bool = True,
    reraise: bool = False,
    context: str = ""
):
    """
    安全执行上下文管理器

    Args:
        log_errors: 是否记录错误
        reraise: 是否重新抛出异常
        context: 上下文信息
    """
    try:
        yield
    except Exception as e:
        if log_errors:
            error_msg = f"{context} 上下文异常" if context else "上下文异常"
            append_runtime_log(
                f"{error_msg}: {type(e).__name__}: {e}",
                debug=True
            )
        if reraise:
            raise


def log_performance(
    func: Callable[..., T],
    threshold_ms: float = 100.0,
    log_slow: bool = True
) -> Callable[..., T]:
    """
    性能监控装饰器

    Args:
        func: 要监控的函数
        threshold_ms: 慢操作阈值（毫秒）
        log_slow: 是否记录慢操作
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> T:
        import time
        start_time = time.perf_counter()

        try:
            result = func(*args, **kwargs)
            return result
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            if log_slow and elapsed_ms > threshold_ms:
                append_runtime_log(
                    f"慢操作: {func.__qualname__} 耗时 {elapsed_ms:.1f}ms",
                    debug=True
                )
    return wrapper


# 便捷函数
def safe_call_result(*args, **kwargs) -> SafeExecutionResult:
    """返回 SafeExecutionResult 的 safe_call 版本"""
    return safe_call(*args, **kwargs)


def ignore_errors(
    func: Callable[..., T],
    default: Optional[T] = None,
    log_level: str = "debug"
) -> Callable[..., T]:
    """忽略所有错误的便捷装饰器"""
    return safe_method(
        log_errors=(log_level != "none"),
        default_return=default
    )(func)