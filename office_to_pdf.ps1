param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
)

$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$destinationFolder = [System.IO.Path]::GetDirectoryName($destinationPath)
[System.IO.Directory]::CreateDirectory($destinationFolder) | Out-Null
$extension = [System.IO.Path]::GetExtension($sourcePath).ToLowerInvariant()

function Release-ComObject($value) {
    if ($null -ne $value) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($value)
    }
}

switch ($extension) {
    '.docx' {
        $application = $null
        $document = $null
        try {
            $application = New-Object -ComObject Word.Application
            $application.Visible = $false
            $application.DisplayAlerts = 0
            $document = $application.Documents.Open($sourcePath, $false, $true)
            $document.ExportAsFixedFormat($destinationPath, 17)
        }
        finally {
            if ($null -ne $document) { $document.Close($false) }
            if ($null -ne $application) { $application.Quit() }
            Release-ComObject $document
            Release-ComObject $application
        }
    }
    '.pptx' {
        $application = $null
        $presentation = $null
        try {
            $application = New-Object -ComObject PowerPoint.Application
            $presentation = $application.Presentations.Open(
                $sourcePath, $true, $true, $false
            )
            $presentation.SaveAs($destinationPath, 32)
        }
        finally {
            if ($null -ne $presentation) { $presentation.Close() }
            if ($null -ne $application) { $application.Quit() }
            Release-ComObject $presentation
            Release-ComObject $application
        }
    }
    '.xlsx' {
        $application = $null
        $workbook = $null
        try {
            $application = New-Object -ComObject Excel.Application
            $application.Visible = $false
            $application.DisplayAlerts = $false
            $workbook = $application.Workbooks.Open($sourcePath, 0, $true)
            $workbook.ExportAsFixedFormat(0, $destinationPath)
        }
        finally {
            if ($null -ne $workbook) { $workbook.Close($false) }
            if ($null -ne $application) { $application.Quit() }
            Release-ComObject $workbook
            Release-ComObject $application
        }
    }
    default {
        throw "Unsupported Microsoft Office extension: $extension"
    }
}

if (-not (Test-Path -LiteralPath $destinationPath -PathType Leaf)) {
    throw "Office did not create the requested PDF: $destinationPath"
}
