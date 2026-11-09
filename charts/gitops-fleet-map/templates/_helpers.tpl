{{- define "fleetmap.name" -}}{{ .Chart.Name }}{{- end }}

{{- define "fleetmap.fullname" -}}
{{- if contains .Chart.Name .Release.Name }}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end }}
{{- end }}

{{- define "fleetmap.selector" -}}
app.kubernetes.io/name: {{ include "fleetmap.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "fleetmap.labels" -}}
{{ include "fleetmap.selector" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{/* Fails the render unless sigsUrl is set and exactly one token source is set. */}}
{{- define "fleetmap.validate" -}}
{{- $_ := required "sigsUrl is required: set the web URL of the sigs group, e.g. --set sigsUrl=https://gitlab.example/org/day2/sigs" .Values.sigsUrl -}}
{{- if and .Values.token.value .Values.token.existingSecret }}{{ fail "set only one token source: token.value or token.existingSecret, not both" }}{{ end -}}
{{- if not (or .Values.token.value .Values.token.existingSecret) }}{{ fail "a token is required: set token.existingSecret (preferred) or token.value" }}{{ end -}}
{{- end }}

{{- define "fleetmap.tokenSecret" -}}{{ .Values.token.existingSecret | default (include "fleetmap.fullname" .) }}{{- end }}
{{- define "fleetmap.caConfigMap" -}}{{ .Values.ca.existingConfigMap | default (printf "%s-ca" (include "fleetmap.fullname" .)) }}{{- end }}
