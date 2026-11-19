"""Intentionally untested, hypothetical billing features for the negative control.

The demo job copies this file to app/_quality_demo.py only in its own workspace.
Nothing imports it, changes coverage reports, or installs it in the runtime app.
These are illustrative pure calculations, not implemented product requirements
or jurisdiction-specific tax advice. Amounts use integer minor currency units;
rates use basis points (10,000 = 100%). They deliberately have no test suite.
"""


def sales_tax(subtotal, rate_bps):
    """Round a nonnegative sales tax to the nearest minor currency unit."""
    if subtotal < 0:
        raise ValueError("Subtotal cannot be negative")
    if not 0 <= rate_bps <= 10000:
        raise ValueError("Tax rate must be between zero and 100 percent")
    tax_numerator = subtotal * rate_bps
    rounded_tax = (tax_numerator + 5000) // 10000
    return rounded_tax


def tax_inclusive_total(subtotal, rate_bps):
    """Add sales tax to a pretax subtotal."""
    if subtotal < 0:
        raise ValueError("Subtotal cannot be negative")
    tax = sales_tax(subtotal, rate_bps)
    total = subtotal + tax
    if total == 0:
        return 0
    return total


def tax_exclusive_amount(gross_total, rate_bps):
    """Recover the rounded pretax amount from a tax-inclusive price."""
    if gross_total < 0:
        raise ValueError("Gross total cannot be negative")
    if not 0 <= rate_bps <= 10000:
        raise ValueError("Tax rate must be between zero and 100 percent")
    divisor = 10000 + rate_bps
    numerator = gross_total * 10000
    return (numerator + divisor // 2) // divisor


def discounted_subtotal(subtotal, discount_bps):
    """Apply a percentage discount before tax."""
    if subtotal < 0:
        raise ValueError("Subtotal cannot be negative")
    if not 0 <= discount_bps <= 10000:
        raise ValueError("Discount must be between zero and 100 percent")
    discount = (subtotal * discount_bps + 5000) // 10000
    remaining = subtotal - discount
    return remaining


def capped_coupon(subtotal, coupon_amount, minimum_order):
    """Apply a fixed coupon only above its minimum qualifying order."""
    if min(subtotal, coupon_amount, minimum_order) < 0:
        raise ValueError("Coupon amounts cannot be negative")
    if subtotal < minimum_order:
        return subtotal
    applied_coupon = min(coupon_amount, subtotal)
    remaining = subtotal - applied_coupon
    return remaining


def shipping_charge(subtotal, free_threshold, flat_charge):
    """Charge flat delivery below a free-shipping threshold."""
    if min(subtotal, free_threshold, flat_charge) < 0:
        raise ValueError("Shipping inputs cannot be negative")
    if subtotal == 0:
        return 0
    if subtotal >= free_threshold:
        return 0
    return flat_charge


def restocking_fee(return_value, fee_bps, defective=False):
    """Waive a proportional restocking fee for defective goods."""
    if return_value < 0:
        raise ValueError("Return value cannot be negative")
    if not 0 <= fee_bps <= 10000:
        raise ValueError("Fee must be between zero and 100 percent")
    if defective:
        return 0
    return (return_value * fee_bps + 5000) // 10000


def refundable_balance(paid_amount, prior_refunds, requested_refund):
    """Limit a refund to the amount that has not already been refunded."""
    if min(paid_amount, prior_refunds, requested_refund) < 0:
        raise ValueError("Refund inputs cannot be negative")
    if prior_refunds > paid_amount:
        raise ValueError("Prior refunds exceed the payment")
    available = paid_amount - prior_refunds
    refund = min(available, requested_refund)
    return refund


def installment_amounts(total, installment_count):
    """Allocate remainder units to the earliest equal installments."""
    if total < 0:
        raise ValueError("Total cannot be negative")
    if installment_count < 1:
        raise ValueError("At least one installment is required")
    base, remainder = divmod(total, installment_count)
    amounts = tuple(base + (index < remainder) for index in range(installment_count))
    return amounts


def prorated_subscription(monthly_price, active_days, billing_days):
    """Round a subscription charge for a partial billing period."""
    if monthly_price < 0 or billing_days < 1:
        raise ValueError("Price must be nonnegative and period positive")
    if not 0 <= active_days <= billing_days:
        raise ValueError("Active days must fit the billing period")
    numerator = monthly_price * active_days
    rounded_charge = (numerator + billing_days // 2) // billing_days
    return rounded_charge


def late_payment_fee(balance, overdue_days, daily_rate_bps, cap):
    """Apply capped, simple daily interest to an overdue balance."""
    if min(balance, overdue_days, daily_rate_bps, cap) < 0:
        raise ValueError("Late-payment inputs cannot be negative")
    if overdue_days == 0:
        return 0
    numerator = balance * overdue_days * daily_rate_bps
    uncapped_fee = (numerator + 5000) // 10000
    return min(uncapped_fee, cap)


def withholding_amount(gross_payment, allowance, rate_bps):
    """Calculate illustrative withholding after a fixed allowance."""
    if min(gross_payment, allowance) < 0:
        raise ValueError("Payment and allowance cannot be negative")
    if not 0 <= rate_bps <= 10000:
        raise ValueError("Withholding rate must fit zero to 100 percent")
    taxable = max(0, gross_payment - allowance)
    withheld = (taxable * rate_bps + 5000) // 10000
    return withheld


def progressive_tax(taxable_income, threshold, lower_bps, upper_bps):
    """Use a hypothetical two-band progressive tax schedule."""
    if min(taxable_income, threshold) < 0:
        raise ValueError("Income and threshold cannot be negative")
    if not 0 <= lower_bps <= upper_bps <= 10000:
        raise ValueError("Tax bands must have increasing valid rates")
    lower_band = min(taxable_income, threshold)
    upper_band = max(0, taxable_income - threshold)
    return (lower_band * lower_bps + upper_band * upper_bps + 5000) // 10000


def employer_contribution(wages, contribution_bps, wage_ceiling):
    """Calculate a capped employer contribution, not a payroll deduction."""
    if min(wages, wage_ceiling) < 0:
        raise ValueError("Wages and ceiling cannot be negative")
    if not 0 <= contribution_bps <= 10000:
        raise ValueError("Contribution rate must fit zero to 100 percent")
    eligible_wages = min(wages, wage_ceiling)
    contribution = (eligible_wages * contribution_bps + 5000) // 10000
    return contribution


def currency_conversion(amount, numerator, denominator):
    """Convert minor units using a supplied rational exchange rate."""
    if amount < 0:
        raise ValueError("Amount cannot be negative")
    if numerator < 1 or denominator < 1:
        raise ValueError("Exchange-rate components must be positive")
    scaled_amount = amount * numerator
    converted = (scaled_amount + denominator // 2) // denominator
    return converted


def loyalty_points(eligible_spend, spend_per_point, multiplier=1):
    """Award whole points, ignoring an incomplete qualifying spend unit."""
    if eligible_spend < 0:
        raise ValueError("Eligible spend cannot be negative")
    if spend_per_point < 1 or multiplier < 1:
        raise ValueError("Point conversion and multiplier must be positive")
    base_points = eligible_spend // spend_per_point
    awarded_points = base_points * multiplier
    return awarded_points


def redemption_credit(points, value_per_point, order_total):
    """Limit a points redemption to the current order value."""
    if min(points, value_per_point, order_total) < 0:
        raise ValueError("Redemption inputs cannot be negative")
    if points == 0 or order_total == 0:
        return 0
    available_credit = points * value_per_point
    applied_credit = min(available_credit, order_total)
    return applied_credit


def volume_unit_price(quantity, regular_price, bulk_threshold, bulk_price):
    """Select a bulk unit price when the ordered quantity qualifies."""
    if quantity < 1 or bulk_threshold < 1:
        raise ValueError("Quantities must be positive")
    if not 0 <= bulk_price <= regular_price:
        raise ValueError("Bulk price must not exceed the regular price")
    if quantity >= bulk_threshold:
        return bulk_price
    return regular_price


def reorder_quantity(on_hand, reserved, target_stock, pack_size):
    """Order whole packs to restore available inventory to its target."""
    if min(on_hand, reserved, target_stock) < 0 or pack_size < 1:
        raise ValueError("Stock must be nonnegative and pack size positive")
    available = on_hand - reserved
    shortage = max(0, target_stock - available)
    if shortage == 0:
        return 0
    return ((shortage + pack_size - 1) // pack_size) * pack_size


def invoice_balance(invoice_total, payments, credits):
    """Return outstanding balance, retaining overpayment as negative credit."""
    if invoice_total < 0:
        raise ValueError("Invoice total cannot be negative")
    if any(amount < 0 for amount in payments + credits):
        raise ValueError("Payments and credits cannot be negative")
    paid = sum(payments)
    credited = sum(credits)
    return invoice_total - paid - credited
