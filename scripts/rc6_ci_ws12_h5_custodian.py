"""Offline candidate-evidence custody; never an execution attestation.

The future trusted runner must own this object, acquire inputs outside the
candidate process and keep it private. No supplied input proves provenance.
"""
from scripts.rc6_ci_ws12_oci_supervisor import OCIHOLD, review_private_files


class CustodyHold(ValueError):
    """Evidence is incomplete, out of order or not safely reviewable."""


_ORDER = ("full-collected.json", "subset-collected.json",
          "full-executed.json", "junit.xml")
_LIMIT = 16 * 1024 * 1024


class Custody:
    def __init__(self):
        self._files = []
        self._reported = False

    def capture(self, name, content):
        if (len(self._files) >= len(_ORDER) or name != _ORDER[len(self._files)]
                or type(content) is not bytes or len(content) > _LIMIT):
            raise CustodyHold("CAPTURE_REJECTED")
        self._files.append(content)

    def review(self, *, trusted_main_sha, candidate_sha, tree, qa_image_digest,
               run_id, attempt, pytest_exit_code):
        self._reported = False
        if len(self._files) != 4:
            raise CustodyHold("INCOMPLETE")
        try:
            review_private_files(full_collected=self._files[0],
                                 subset_collected=self._files[1],
                                 full_executed=self._files[2], junit=self._files[3],
                                 pytest_exit_code=pytest_exit_code,
                                 trusted_main_sha=trusted_main_sha,
                                 candidate_sha=candidate_sha, tree=tree,
                                 qa_image_digest=qa_image_digest,
                                 run_id=run_id, attempt=attempt)
        except (OCIHOLD, TypeError, ValueError):
            raise CustodyHold("REVIEW_REJECTED") from None
        self._reported = True
        return self.receipt()

    def receipt(self):
        return {"status": "HOLD", "candidate_reported_pass": self._reported,
                "physical_execution_observed": False,
                "af_unix_independently_verified": False,
                "qa_certified": False, "automatic_go": False}
