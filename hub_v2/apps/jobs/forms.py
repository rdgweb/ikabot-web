"""
Forms for job creation — dynamic form builder based on ACTION_CATALOG schemas.
"""

import json

from django import forms

from apps.accounts.models import Account, GameAccount
from core.contracts import (
    ACTION_CATALOG,
    BUILDING_CATALOG,
    DONATION_CHOICES,
    FIELD_BOOL,
    FIELD_BUILDING_SELECT,
    FIELD_CHOICE,
    FIELD_CITY_SELECT,
    FIELD_DONATION_TYPE,
    FIELD_INT,
    FIELD_RESOURCE_TYPE,
    FIELD_STR,
    RESOURCE_CHOICES,
    TRANSPORT_LOAD_CHOICES,
)


class JobCreateForm(forms.Form):
    """
    Dynamic form that builds fields from an action's input schema.
    Also handles GameAccount + action selection.
    """

    game_account = forms.UUIDField(widget=forms.HiddenInput())
    action_code = forms.IntegerField(widget=forms.HiddenInput())

    def __init__(self, *args, action_code=None, game_account=None, cities=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.action_meta = ACTION_CATALOG.get(action_code, {})
        self.game_account_obj = game_account
        self.cities = cities or []
        self.selected_city_id = self._extract_selected_city_id()

        # Build dynamic fields from schema
        for field_def in self.action_meta.get("inputs", []):
            field = self._build_field(field_def)
            if field:
                self.fields[field_def["key"]] = field

    def _extract_selected_city_id(self):
        raw = None
        if self.is_bound:
            raw = self.data.get("city")
        elif isinstance(self.initial, dict):
            raw = self.initial.get("city")
        return str(raw).strip() if raw not in (None, "") else ""

    def _get_city_buildings(self):
        selected = self.selected_city_id
        if not selected:
            return []
        for city in self.cities:
            if str(city.get("id")) != selected:
                continue
            buildings = city.get("buildings") or []
            out = []
            for item in buildings:
                building_id = str(item.get("building") or "").strip()
                if not building_id or building_id == "empty":
                    continue
                out.append({
                    "position": int(item.get("position", 0) or 0),
                    "building": building_id,
                    "level": int(item.get("level", 0) or 0),
                })
            return out
        return []

    def _build_field(self, field_def):
        """Create a Django form field from a schema field definition."""
        ftype = field_def["type"]
        label = field_def.get("label", field_def["key"])
        required = field_def.get("required", False)
        help_text = field_def.get("help", "")
        placeholder = field_def.get("placeholder", "")

        base_attrs = {"class": "form-input", "placeholder": placeholder}

        if ftype == FIELD_INT:
            attrs = {**base_attrs}
            if "min" in field_def:
                attrs["min"] = field_def["min"]
            if "max" in field_def:
                attrs["max"] = field_def["max"]
            return forms.IntegerField(
                label=label,
                required=required,
                help_text=help_text,
                widget=forms.NumberInput(attrs=attrs),
                min_value=field_def.get("min"),
                max_value=field_def.get("max"),
                initial=field_def.get("default"),
            )

        if ftype == FIELD_STR:
            return forms.CharField(
                label=label,
                required=required,
                help_text=help_text,
                widget=forms.TextInput(attrs=base_attrs),
                initial=field_def.get("default", ""),
            )

        if ftype == FIELD_BOOL:
            return forms.BooleanField(
                label=label,
                required=False,
                initial=field_def.get("default", False),
                help_text=help_text,
                widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            )

        if ftype == FIELD_CITY_SELECT:
            multiple = field_def.get("multiple", False)
            choices = [(c.get("id", i), c.get("name", f"Cidade {i+1}")) for i, c in enumerate(self.cities)]
            if multiple:
                return forms.MultipleChoiceField(
                    label=label,
                    required=required,
                    choices=choices,
                    help_text=help_text,
                    widget=forms.CheckboxSelectMultiple(attrs={"class": "city-checkbox"}),
                )
            else:
                return forms.ChoiceField(
                    label=label,
                    required=required,
                    choices=[("", "Selecione...")] + choices,
                    help_text=help_text,
                    widget=forms.Select(attrs={**base_attrs}),
                )

        if ftype == FIELD_BUILDING_SELECT:
            buildings = self._get_city_buildings()
            choices = [("", "Selecione...")]
            for item in buildings:
                info = BUILDING_CATALOG.get(item["building"]) or {}
                label = info.get("name") or item["building"]
                choices.append((
                    str(item["position"]),
                    f"{label} (lvl {item['level']}) · pos {item['position']}",
                ))
            return forms.ChoiceField(
                label=label,
                required=required,
                choices=choices,
                help_text=help_text,
                widget=forms.Select(attrs={**base_attrs}),
            )

        if ftype == FIELD_RESOURCE_TYPE:
            choices = [(str(idx), name) for idx, name, _ in RESOURCE_CHOICES]
            return forms.ChoiceField(
                label=label,
                required=required,
                choices=choices,
                help_text=help_text,
                widget=forms.RadioSelect(attrs={"class": "resource-radio"}),
            )

        if ftype == FIELD_DONATION_TYPE:
            choices = [(key, name) for key, name, _ in DONATION_CHOICES]
            return forms.MultipleChoiceField(
                label=label,
                required=required,
                choices=choices,
                help_text=help_text,
                widget=forms.CheckboxSelectMultiple(attrs={"class": "donation-checkbox"}),
            )

        if ftype == FIELD_CHOICE:
            choices = [(c[0], c[1]) for c in field_def.get("choices", [])]
            if field_def.get("key") == "building_type" and not choices:
                choices = [
                    (building_id, info.get("name", building_id))
                    for building_id, info in sorted(
                        BUILDING_CATALOG.items(),
                        key=lambda entry: entry[1].get("name", entry[0]),
                    )
                ]
            if field_def.get("key") == "post_production_mode":
                return forms.ChoiceField(
                    label=label,
                    required=required,
                    choices=choices,
                    help_text=help_text,
                    widget=forms.RadioSelect(attrs={"class": "choice-radio"}),
                    initial=field_def.get("default", "preserve"),
                )
            widget = forms.Select(attrs={**base_attrs})
            field_choices = [("", "Selecione...")] + choices
            if field_def.get("key") in {
                "donation_method",
                "transport_load_percent",
                "research_reduction",
                "build_time_reduction",
                "strategy",
                "queue_strategy",
                "post_production_mode",
            }:
                widget = forms.RadioSelect(attrs={"class": "choice-radio"})
                field_choices = choices
            return forms.ChoiceField(
                label=label,
                required=required,
                choices=field_choices,
                help_text=help_text,
                widget=widget,
                initial=field_def.get("default", ""),
            )

        # Fallback: text input
        return forms.CharField(
            label=label,
            required=required,
            help_text=help_text,
            widget=forms.TextInput(attrs=base_attrs),
        )

    def get_inputs_json(self) -> dict:
        """Extract the action inputs from cleaned_data (excluding meta fields)."""
        meta_keys = {"game_account", "action_code"}
        inputs = {}
        for key, value in self.cleaned_data.items():
            if key in meta_keys:
                continue
            # Convert MultipleChoiceField lists to proper format
            if isinstance(value, list):
                inputs[key] = value
            elif value is not None and value != "":
                inputs[key] = value
        return inputs
