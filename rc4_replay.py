from decimal import Decimal,InvalidOperation
def D(v):
    try:x=Decimal(str(v))
    except (InvalidOperation,TypeError,ValueError) as e:raise ValueError('INVALID_NUMBER') from e
    if not x.is_finite():raise ValueError('INVALID_NUMBER')
    return x
def net_breakeven(gain,loss):
    g,l=D(gain),D(loss); return None if g<=0 or l<=0 else l/(g+l)
def true_range(cur,prev):
    h,l,pc=D(cur['high']),D(cur['low']),D(prev['close']); return max(h-l,abs(h-pc),abs(l-pc))
def atr(candles,period=20):
    if type(period) is not int or period<2:raise ValueError('INVALID_ATR_PERIOD')
    if len(candles)<period+1:return None
    vals=[true_range(candles[i],candles[i-1]) for i in range(1,len(candles))]; return sum(vals[-period:],Decimal(0))/Decimal(period)
def slippage_bps(reference,fill,side):
    r,f=D(reference),D(fill); adverse=(f-r) if str(side).upper().startswith('BUY') else (r-f); return adverse/r*Decimal(10000)
