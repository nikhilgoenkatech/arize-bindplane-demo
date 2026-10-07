{{- define "payments-demo.fullname" -}}
{{- .Release.Name | trunc 42 | trimSuffix "-" -}}
{{- end -}}

{{- define "payments-demo.labels" -}}
app.kubernetes.io/name: payments-demo
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: payments-demo
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
