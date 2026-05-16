"""
Payment provider subsystem — Layer 5 of the checkout defense-in-depth.

Public surface:
  from .payment import registry
  from .payment.base import (
      PaymentProvider, PaymentHold,
      PaymentProviderUnavailable,
      PaymentRetryableError, PaymentPermanentError,
  )
  from .payment.stripe_provider import StripeProvider

Read scripts/checkout/payment/base.py for the Protocol contract before adding
a new provider.
"""
