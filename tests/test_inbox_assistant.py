import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inbox_assistant import Email, categorize, extract_deadlines, score_email, triage  # noqa: E402


class TestInboxAssistant(unittest.TestCase):
    def test_interview_email_is_urgent_career(self):
        e = Email("recruiter@co.com", "Interview Invitation", "Schedule an interview next week.")
        result = triage([e])[0]
        self.assertEqual(result.priority, "URGENT")
        self.assertEqual(result.category, "Career")

    def test_promotion_scores_negative(self):
        e = Email("newsletter@shop.com", "BIG SALE 50% OFF!!", "Limited time. Unsubscribe here.")
        score, _ = score_email(e)
        self.assertLess(score, 0)
        self.assertEqual(categorize(e), "Promotion")

    def test_subject_keywords_weigh_more_than_body(self):
        in_subject = Email("a@b.com", "Deadline", "hello")
        in_body = Email("a@b.com", "hello", "deadline")
        self.assertGreater(score_email(in_subject)[0], score_email(in_body)[0])

    def test_edu_sender_boost(self):
        edu = Email("prof@school.edu", "hi", "hi")
        other = Email("prof@school.com", "hi", "hi")
        self.assertGreater(score_email(edu)[0], score_email(other)[0])

    def test_deadline_extraction(self):
        text = "Submit by Friday midnight, or at the latest before Nov 12. Forms due 11/15."
        self.assertEqual(extract_deadlines(text), ["Friday midnight", "Nov 12", "11/15"])

    def test_results_sorted_by_score(self):
        emails = [
            Email("shop@x.com", "sale", "promo"),
            Email("prof@u.edu", "Urgent exam deadline", "due tomorrow"),
        ]
        results = triage(emails)
        self.assertEqual(results[0].sender, "prof@u.edu")


if __name__ == "__main__":
    unittest.main()
