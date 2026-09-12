{{- define "parlio.labels" -}}
app.kubernetes.io/part-of: parlio
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
parlio.io/env: {{ .Values.env }}
{{- end }}
