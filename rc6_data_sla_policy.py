from dataclasses import dataclass
import os

@dataclass(frozen=True)
class DataSLA:
    cadence_seconds:int
    warning_after_seconds:int
    max_staleness_seconds:int
    def __post_init__(self):
        if min(self.cadence_seconds,self.warning_after_seconds,self.max_staleness_seconds)<=0:
            raise ValueError('DATA_SLA_NON_POSITIVE')
        if self.warning_after_seconds<self.cadence_seconds:
            raise ValueError('DATA_SLA_WARNING_BEFORE_CADENCE')
        if self.max_staleness_seconds<self.warning_after_seconds:
            raise ValueError('DATA_SLA_MAX_BEFORE_WARNING')

def _positive(name,default):
    v=int(os.getenv(name,str(default)))
    if v<=0:
        raise ValueError(name+'_NON_POSITIVE')
    return v

def ppi_background_ingest_sla():
    c=_positive('PPI_BACKGROUND_INGEST_SECONDS',21600)
    w=_positive('PPI_BACKGROUND_INGEST_WARN_AFTER_SECONDS',c)
    m=_positive('PPI_BACKGROUND_INGEST_MAX_STALENESS_SECONDS',w)
    return DataSLA(c,w,m)
