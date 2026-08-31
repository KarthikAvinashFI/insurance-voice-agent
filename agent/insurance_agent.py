from __future__ import annotations

from typing import Literal

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ToolError

from .prompt import build_instructions
from .state import UNDERWRITER_TOPICS, GuardError, PolicyState
from .tools_client import ToolsAPIError, ToolsClient

LossType = Literal["collision", "comprehensive", "glass", "theft", "weather", "other"]
CoverageType = Literal[
    "collision", "comprehensive", "glass", "rental", "roadside", "liability"
]


def _tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, (GuardError, ToolsAPIError)):
        return ToolError(str(exc))
    return ToolError("That action could not be completed. Offer to retry or transfer.")


class InsuranceAgent(Agent):
    """LiveKit agent whose tools enforce verify-before-disclose and confirm-before-commit."""

    def __init__(self, state: PolicyState, client: ToolsClient, context: dict) -> None:
        self.state = state
        self.client = client
        self.context = context
        super().__init__(instructions=build_instructions(context))

    async def on_enter(self) -> None:
        if self.state.first_name:
            greeting = (
                f"Hi {self.state.first_name}, thanks for calling Meridian Mutual. "
                "How can I help you today?"
            )
        else:
            greeting = (
                "Thanks for calling Meridian Mutual. I can help with claims, cover "
                "and payments. What do you need today?"
            )
        await self.session.generate_reply(
            instructions=f'Say exactly this greeting: "{greeting}"',
            allow_interruptions=True,
        )

    def _local(self, name: str, arguments: dict, output: dict) -> None:
        self.client.record_local(name, arguments, output)

    # ------------------------------------------------------------ identity

    @function_tool()
    async def verify_identity(
        self,
        policy_number: str,
        date_of_birth: str | None = None,
        zip_code: str | None = None,
    ) -> dict:
        """Verify the caller with their policy number plus a date of birth (YYYY-MM-DD) or postcode. Required before sharing anything."""
        try:
            result = await self.client.call(
                "verify_identity",
                policy_number=policy_number,
                date_of_birth=date_of_birth,
                zip_code=zip_code,
            )
            if result.get("verified"):
                self.state.mark_verified(result)
            return result
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def send_verification_code(self) -> dict:
        """Send an SMS code as an alternative identity check when the caller cannot recall their details."""
        try:
            return await self.client.call("send_otp", phone=self.state.caller_ani)
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def check_verification_code(self, code: str) -> dict:
        """Check the SMS code the caller read back. Never guess or infer a code."""
        try:
            result = await self.client.call(
                "verify_otp", phone=self.state.caller_ani, code=code
            )
            if result.get("verified") and self.state.policy_id:
                self.state.auth_level = "identity_verified"
            return result
        except Exception as exc:
            raise _tool_error(exc) from exc

    # ------------------------------------------------------------ policy

    @function_tool()
    async def get_policy_summary(self) -> dict:
        """Get status, renewal date and premium for the verified policy."""
        try:
            self.state.ensure_verified("policy details")
            return await self.client.call(
                "get_policy_summary", policy_id=self.state.ensure_policy()
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def get_vehicles(self) -> dict:
        """List the vehicles on the policy by year, make and model."""
        try:
            self.state.ensure_verified("vehicle details")
            return await self.client.call(
                "get_vehicles", policy_id=self.state.ensure_policy()
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def get_coverage(self, coverage_type: CoverageType) -> dict:
        """Answer whether a specific cover is included and what its deductible and limit are."""
        try:
            self.state.ensure_verified("cover details")
            return await self.client.call(
                "get_coverage",
                policy_id=self.state.ensure_policy(),
                coverage_type=coverage_type,
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def list_coverages(self) -> dict:
        """List everything the policy covers, for a caller who asks broadly what they have."""
        try:
            self.state.ensure_verified("cover details")
            return await self.client.call(
                "list_coverages", policy_id=self.state.ensure_policy()
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    # ------------------------------------------------------------ claims

    @function_tool()
    async def get_claim_status(self, claim_ref: str) -> dict:
        """Get the status, adjuster and next step for a claim the caller names by reference."""
        try:
            self.state.ensure_verified("claim details")
            return await self.client.call("get_claim_status", claim_ref=claim_ref)
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def list_claims(self) -> dict:
        """List recent claims on the policy when the caller cannot recall a reference."""
        try:
            self.state.ensure_verified("claim details")
            return await self.client.call(
                "list_claims", policy_id=self.state.ensure_policy()
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def record_loss_detail(
        self,
        loss_type: LossType | None = None,
        loss_date: str | None = None,
        loss_location: str | None = None,
        description: str | None = None,
        other_party: str | None = None,
    ) -> dict:
        """Record one or more loss details as the caller gives them. Call this each time a new detail arrives."""
        try:
            draft = self.state.draft_loss(
                loss_type=loss_type,
                loss_date=loss_date,
                loss_location=loss_location,
                description=description,
                other_party=other_party,
            )
            result = {"recorded": draft, "still_needed": self.state.missing_loss_fields()}
            self._local(
                "record_loss_detail",
                {
                    "loss_type": loss_type,
                    "loss_date": loss_date,
                    "loss_location": loss_location,
                    "description": description,
                    "other_party": other_party,
                },
                result,
            )
            return result
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def prepare_claim_filing(self) -> dict:
        """Build the exact loss read-back and a one-time token. Read the summary, then ask for an explicit yes."""
        try:
            token, summary = self.state.prepare_fnol()
            result = {"confirmation_token": token, "summary_to_read": summary}
            self._local("prepare_claim_filing", {}, result)
            return result
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def file_claim(
        self,
        context: RunContext,
        confirmation_token: str,
        caller_explicitly_confirmed: bool,
    ) -> dict:
        """File the claim only after reading the prepared summary and hearing an explicit yes."""
        context.disallow_interruptions()
        try:
            snapshot = self.state.authorize_fnol(
                confirmation_token, caller_explicitly_confirmed
            )
            result = await self.client.call(
                "file_claim",
                policy_id=self.state.ensure_policy(),
                loss_type=snapshot["loss_type"],
                loss_date=snapshot["loss_date"],
                loss_location=snapshot["loss_location"],
                description=snapshot["description"],
                other_party=snapshot.get("other_party"),
                idempotency_key=confirmation_token,
                _trace_payload={
                    "confirmation_token": confirmation_token,
                    "caller_explicitly_confirmed": caller_explicitly_confirmed,
                },
            )
            self.state.filed_claim_ref = result.get("claim_ref")
            return result
        except Exception as exc:
            raise _tool_error(exc) from exc

    # ------------------------------------------------------------ money

    @function_tool()
    async def get_balance(self) -> dict:
        """Get what is owed on the policy and when it is due."""
        try:
            self.state.ensure_verified("billing details")
            return await self.client.call(
                "get_balance", policy_id=self.state.ensure_policy()
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def send_payment_link_sms(self, amount: float | None = None) -> dict:
        """Text a secure payment link. Use this instead of ever taking card details by voice."""
        try:
            self.state.ensure_verified("a payment")
            result = await self.client.call(
                "send_payment_link_sms",
                phone=self.state.caller_ani,
                policy_id=self.state.ensure_policy(),
                amount=amount,
            )
            self.state.last_payment_link_id = result.get("link_id")
            return result
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def check_payment_link_status(self) -> dict:
        """Check whether the texted payment link is ready for the caller to use."""
        if not self.state.last_payment_link_id:
            raise ToolError("No payment link has been sent on this call.")
        try:
            return await self.client.call(
                "check_payment_link_status", link_id=self.state.last_payment_link_id
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def send_id_card_sms(self) -> dict:
        """Text a copy of the insurance ID card for the verified policy."""
        try:
            self.state.ensure_verified("an ID card")
            return await self.client.call(
                "send_id_card_sms", policy_id=self.state.ensure_policy()
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    # ------------------------------------------------------------ cancellation

    @function_tool()
    async def prepare_cancellation(self) -> dict:
        """Build the cancellation read-back and a one-time token. Read it, then ask for an explicit yes."""
        try:
            token, summary = self.state.prepare_cancellation()
            result = {"confirmation_token": token, "summary_to_read": summary}
            self._local("prepare_cancellation", {}, result)
            return result
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def cancel_policy(
        self,
        context: RunContext,
        confirmation_token: str,
        caller_explicitly_confirmed: bool,
        reason: str,
    ) -> dict:
        """Cancel the policy only after the read-back and an explicit yes. Never on a hint."""
        context.disallow_interruptions()
        try:
            self.state.authorize_cancellation(
                confirmation_token, caller_explicitly_confirmed
            )
            return await self.client.call(
                "request_policy_cancellation",
                policy_id=self.state.ensure_policy(),
                reason=reason,
                idempotency_key=confirmation_token,
                _trace_payload={
                    "confirmation_token": confirmation_token,
                    "caller_explicitly_confirmed": caller_explicitly_confirmed,
                },
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    # ------------------------------------------------------------ handoff

    @function_tool()
    async def transfer_to_human(self, reason: str, queue: str = "general") -> dict:
        """Hand off for underwriter changes, denial disputes, injuries, legal matters, or a failed service."""
        try:
            return await self.client.call(
                "transfer_to_human",
                phone=self.state.caller_ani,
                reason=reason,
                queue=queue,
            )
        except Exception as exc:
            raise _tool_error(exc) from exc

    @function_tool()
    async def refuse_underwriter_change(self, topic: str) -> dict:
        """Decline a policy change that needs an underwriter, then transfer. Use for adding or removing vehicles or drivers and for coverage changes."""
        normalised = topic.strip().lower().replace(" ", "_")
        result = {
            "refused": True,
            "topic": normalised,
            "needs_underwriter": normalised in UNDERWRITER_TOPICS or True,
            "spoken_reason": (
                "A change like that has to be priced by an underwriter, "
                "so I cannot make it on this call."
            ),
        }
        self._local("refuse_underwriter_change", {"topic": topic}, result)
        try:
            await self.client.call(
                "transfer_to_human",
                phone=self.state.caller_ani,
                reason=f"underwriter change requested: {normalised}",
                queue="underwriting",
            )
        except Exception as exc:
            raise _tool_error(exc) from exc
        return result
